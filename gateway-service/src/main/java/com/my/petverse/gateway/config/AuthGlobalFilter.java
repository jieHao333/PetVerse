package com.my.petverse.gateway.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.gateway.util.JwtUtil;
import io.jsonwebtoken.Claims;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 网关 JWT 鉴权过滤器
 * 除白名单路径外，所有请求必须携带有效令牌；
 * 校验通过后把用户ID以 X-User-Id 请求头透传给下游服务
 */
@Component
@RequiredArgsConstructor
public class AuthGlobalFilter implements GlobalFilter, Ordered {

    /** 令牌前缀 */
    private static final String BEARER_PREFIX = "Bearer ";

    /** 透传用户ID的请求头名称 */
    private static final String HEADER_USER_ID = "X-User-Id";

    private final JwtUtil jwtUtil;

    private final ObjectMapper objectMapper = new ObjectMapper();

    /** 免鉴权路径白名单，逗号分隔，支持前缀匹配 */
    @Value("${auth.whitelist-paths:/api/user/login,/api/user/register}")
    private String whitelistPaths;

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String path = exchange.getRequest().getURI().getPath();
        // 白名单路径直接放行
        if (isWhitelist(path)) {
            return chain.filter(exchange);
        }
        // 校验 Authorization: Bearer <token>
        String token = resolveToken(exchange.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION));
        if (!StringUtils.hasText(token)) {
            return unauthorized(exchange, "未登录");
        }
        try {
            Claims claims = jwtUtil.parseToken(token);
            Long userId = Long.valueOf(claims.getSubject());
            // 透传用户ID给下游服务
            ServerHttpRequest request = exchange.getRequest().mutate()
                    .header(HEADER_USER_ID, String.valueOf(userId))
                    .build();
            return chain.filter(exchange.mutate().request(request).build());
        } catch (Exception e) {
            return unauthorized(exchange, "登录已失效，请重新登录");
        }
    }

    /** 从请求头中解析 Bearer 令牌 */
    private String resolveToken(String authorization) {
        if (!StringUtils.hasText(authorization) || !authorization.startsWith(BEARER_PREFIX)) {
            return null;
        }
        return authorization.substring(BEARER_PREFIX.length()).trim();
    }

    /** 判断路径是否命中白名单（支持前缀匹配） */
    private boolean isWhitelist(String path) {
        List<String> paths = Arrays.stream(whitelistPaths.split(","))
                .map(String::trim)
                .filter(StringUtils::hasText)
                .collect(Collectors.toList());
        for (String whitelist : paths) {
            if (path.equals(whitelist) || path.startsWith(whitelist)) {
                return true;
            }
        }
        return false;
    }

    /** 返回 401 统一 JSON 结果 */
    private Mono<Void> unauthorized(ServerWebExchange exchange, String msg) {
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(HttpStatus.UNAUTHORIZED);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        String body;
        try {
            // Map.of 不允许 null 值且含 null 会推断失败，改用 LinkedHashMap
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("code", 401);
            result.put("msg", msg);
            result.put("data", null);
            body = objectMapper.writeValueAsString(result);
        } catch (Exception e) {
            body = "{\"code\":401,\"msg\":\"未授权\",\"data\":null}";
        }
        DataBuffer buffer = response.bufferFactory().wrap(body.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }

    @Override
    public int getOrder() {
        return Ordered.HIGHEST_PRECEDENCE;
    }
}
