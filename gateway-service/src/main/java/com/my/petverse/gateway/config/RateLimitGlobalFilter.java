package com.my.petverse.gateway.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 网关令牌桶限流过滤器
 * 在鉴权之后执行：已登录请求按用户ID限流，无身份的请求（登录/注册等白名单）按来源IP限流；
 * AI 对话为长连接流式接口，单独使用更小的桶，避免被单个用户占满；
 * 令牌桶在网关进程内维护，桶空闲超时自动清理，限流组件异常时直接放行
 */
@Slf4j
@Component
public class RateLimitGlobalFilter implements GlobalFilter, Ordered {

    /** 透传用户ID的请求头名称，由鉴权过滤器注入 */
    private static final String HEADER_USER_ID = "X-User-Id";

    /** 走独立限流桶的路径前缀（SSE 流式对话） */
    private static final String AI_PATH_PREFIX = "/api/ai/";

    /** 桶数量上限，超过后触发空闲桶清理 */
    private static final int MAX_BUCKETS = 100_000;

    /** 空闲桶判定时长与清理间隔 */
    private static final long IDLE_NANOS = 10L * 60 * 1000 * 1000 * 1000;

    private static final long CLEANUP_INTERVAL_NANOS = 60L * 1000 * 1000 * 1000;

    private final ConcurrentHashMap<String, TokenBucket> buckets = new ConcurrentHashMap<>();

    private final ObjectMapper objectMapper = new ObjectMapper();

    private volatile long lastCleanupNanos = System.nanoTime();

    /** 限流总开关 */
    @Value("${petverse.gateway.rate-limit.enabled:true}")
    private boolean enabled;

    /** 普通接口令牌桶容量（突发上限） */
    @Value("${petverse.gateway.rate-limit.capacity:100}")
    private int capacity;

    /** 普通接口每秒补充令牌数 */
    @Value("${petverse.gateway.rate-limit.refill-per-second:50}")
    private int refillPerSecond;

    /** AI 流式接口令牌桶容量 */
    @Value("${petverse.gateway.rate-limit.ai-capacity:10}")
    private int aiCapacity;

    /** AI 流式接口每秒补充令牌数 */
    @Value("${petverse.gateway.rate-limit.ai-refill-per-second:2}")
    private int aiRefillPerSecond;

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        if (!enabled) {
            return chain.filter(exchange);
        }
        try {
            String path = exchange.getRequest().getURI().getPath();
            boolean aiScope = path.startsWith(AI_PATH_PREFIX);
            String scope = aiScope ? "ai" : "api";
            String identity = resolveIdentity(exchange);
            String bucketKey = scope + ":" + identity;
            TokenBucket bucket = buckets.computeIfAbsent(bucketKey, key -> new TokenBucket(
                    aiScope ? aiCapacity : capacity,
                    aiScope ? aiRefillPerSecond : refillPerSecond));
            long now = System.nanoTime();
            cleanupIfNeeded(now);
            if (!bucket.tryAcquire(now)) {
                return tooManyRequests(exchange);
            }
        } catch (Exception e) {
            // 限流组件自身异常时放行，避免影响正常请求
            log.warn("限流判断异常，本次请求直接放行", e);
        }
        return chain.filter(exchange);
    }

    /** 已登录请求按用户ID限流，白名单等无身份请求按来源IP限流 */
    private String resolveIdentity(ServerWebExchange exchange) {
        String userId = exchange.getRequest().getHeaders().getFirst(HEADER_USER_ID);
        if (StringUtils.hasText(userId)) {
            return "u:" + userId;
        }
        InetSocketAddress remote = exchange.getRequest().getRemoteAddress();
        return "ip:" + (remote == null || remote.getAddress() == null
                ? "unknown" : remote.getAddress().getHostAddress());
    }

    /** 桶数量超限或到达清理间隔时，移除长期空闲的桶 */
    private void cleanupIfNeeded(long nowNanos) {
        if (buckets.size() <= MAX_BUCKETS && nowNanos - lastCleanupNanos < CLEANUP_INTERVAL_NANOS) {
            return;
        }
        lastCleanupNanos = nowNanos;
        buckets.entrySet().removeIf(entry -> entry.getValue().isIdle(nowNanos));
    }

    /** 返回 429 统一 JSON 结果 */
    private Mono<Void> tooManyRequests(ServerWebExchange exchange) {
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        response.getHeaders().set(HttpHeaders.RETRY_AFTER, "1");
        String body;
        try {
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("code", 429);
            result.put("msg", "请求过于频繁，请稍后再试");
            result.put("data", null);
            body = objectMapper.writeValueAsString(result);
        } catch (Exception e) {
            body = "{\"code\":429,\"msg\":\"请求过于频繁，请稍后再试\",\"data\":null}";
        }
        DataBuffer buffer = response.bufferFactory().wrap(body.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }

    @Override
    public int getOrder() {
        // 排在鉴权过滤器之后：此时请求头已注入用户ID，可直接按用户维度限流
        return Ordered.HIGHEST_PRECEDENCE + 1;
    }

    /** 令牌桶：按时间比例补充令牌，取不到令牌即判定超限 */
    private static final class TokenBucket {

        private final int capacity;

        private final int refillPerSecond;

        private double tokens;

        private long lastRefillNanos;

        private long lastAccessNanos;

        TokenBucket(int capacity, int refillPerSecond) {
            this.capacity = capacity;
            this.refillPerSecond = refillPerSecond;
            this.tokens = capacity;
            this.lastRefillNanos = System.nanoTime();
            this.lastAccessNanos = this.lastRefillNanos;
        }

        synchronized boolean tryAcquire(long nowNanos) {
            long elapsed = nowNanos - lastRefillNanos;
            if (elapsed > 0) {
                tokens = Math.min(capacity, tokens + (double) elapsed / 1_000_000_000L * refillPerSecond);
                lastRefillNanos = nowNanos;
            }
            lastAccessNanos = nowNanos;
            if (tokens < 1.0d) {
                return false;
            }
            tokens -= 1.0d;
            return true;
        }

        synchronized boolean isIdle(long nowNanos) {
            return nowNanos - lastAccessNanos > IDLE_NANOS;
        }
    }
}