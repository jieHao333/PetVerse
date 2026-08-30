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
 * 先剥离客户端携带的 X-User-Id/X-User-Role 请求头防止伪造，
 * 校验令牌后再把解析出的用户ID与角色注入请求头透传给下游服务；
 * 同时拦截外部对内部接口（/internal/）的访问，并对管理端/商家端路径做角色校验
 */
@Component
@RequiredArgsConstructor
public class AuthGlobalFilter implements GlobalFilter, Ordered {

    /** 令牌前缀 */
    private static final String BEARER_PREFIX = "Bearer ";

    /** 透传用户ID的请求头名称 */
    private static final String HEADER_USER_ID = "X-User-Id";

    /** 透传用户角色的请求头名称 */
    private static final String HEADER_USER_ROLE = "X-User-Role";

    /** 管理员角色编码 */
    private static final String ROLE_ADMIN = "ADMIN";

    /** 商家角色编码 */
    private static final String ROLE_MERCHANT = "MERCHANT";

    private final JwtUtil jwtUtil;

    private final ObjectMapper objectMapper = new ObjectMapper();

    /** 免鉴权路径白名单，逗号分隔，支持前缀匹配 */
    @Value("${auth.whitelist-paths:/api/user/login,/api/user/register}")
    private String whitelistPaths;

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String path = exchange.getRequest().getURI().getPath();
        // 内部接口仅供服务间 Feign 调用，禁止经网关从外部访问
        if (path.contains("/internal/")) {
            return forbidden(exchange, "禁止访问内部接口");
        }
        // 剥离客户端自带的身份头防止伪造，下游只认网关注入或服务间透传的值
        ServerWebExchange sanitized = exchange.mutate()
                .request(exchange.getRequest().mutate()
                        .headers(headers -> {
                            headers.remove(HEADER_USER_ID);
                            headers.remove(HEADER_USER_ROLE);
                        })
                        .build())
                .build();
        // 白名单路径直接放行
        if (isWhitelist(path)) {
            return chain.filter(sanitized);
        }
        // 校验 Authorization: Bearer <token>
        String token = resolveToken(sanitized.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION));
        if (!StringUtils.hasText(token)) {
            return unauthorized(exchange, "未登录");
        }
        Long userId;
        String role;
        try {
            Claims claims = jwtUtil.parseToken(token);
            userId = Long.valueOf(claims.getSubject());
            // 存量令牌可能无 role 声明，缺省按普通用户处理
            String claimRole = claims.get("role", String.class);
            role = StringUtils.hasText(claimRole) ? claimRole : "USER";
        } catch (Exception e) {
            return unauthorized(exchange, "登录已失效，请重新登录");
        }
        // 管理端接口仅限管理员访问（角色取自令牌签发时刻，变更角色后需重新登录）
        if (path.startsWith("/api/shop/admin") && !ROLE_ADMIN.equals(role)) {
            return forbidden(exchange, "无管理员权限，若刚变更角色请退出后重新登录");
        }
        // 商家端接口仅限商家/管理员访问
        if (path.startsWith("/api/shop/merchant") && !ROLE_MERCHANT.equals(role) && !ROLE_ADMIN.equals(role)) {
            return forbidden(exchange, "无商家权限，若刚通过入驻审批请退出后重新登录");
        }
        // 透传用户ID与角色给下游服务
        ServerHttpRequest request = sanitized.getRequest().mutate()
                .header(HEADER_USER_ID, String.valueOf(userId))
                .header(HEADER_USER_ROLE, role)
                .build();
        return chain.filter(sanitized.mutate().request(request).build());
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
        return writeJson(exchange, HttpStatus.UNAUTHORIZED, 401, msg);
    }

    /** 返回 403 统一 JSON 结果 */
    private Mono<Void> forbidden(ServerWebExchange exchange, String msg) {
        return writeJson(exchange, HttpStatus.FORBIDDEN, 403, msg);
    }

    /** 写出统一 JSON 错误响应 */
    private Mono<Void> writeJson(ServerWebExchange exchange, HttpStatus status, int code, String msg) {
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(status);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        String body;
        try {
            // Map.of 不允许 null 值且含 null 会推断失败，改用 LinkedHashMap
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("code", code);
            result.put("msg", msg);
            result.put("data", null);
            body = objectMapper.writeValueAsString(result);
        } catch (Exception e) {
            body = "{\"code\":" + code + ",\"msg\":\"" + msg + "\",\"data\":null}";
        }
        DataBuffer buffer = response.bufferFactory().wrap(body.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }

    @Override
    public int getOrder() {
        return Ordered.HIGHEST_PRECEDENCE;
    }
}
