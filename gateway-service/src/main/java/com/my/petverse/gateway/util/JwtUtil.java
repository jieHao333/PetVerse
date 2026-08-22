package com.my.petverse.gateway.util;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;

/**
 * JWT 工具类（网关侧副本）
 * 网关为 WebFlux 应用，无法依赖携带 servlet web 的 common 模块，故保留此轻量副本
 * 逻辑与 common 模块 JwtUtil 保持一致，密钥配置 jwt.secret 必须相同
 */
@Component
public class JwtUtil {

    /** 签名密钥，需与签发方（user-service）配置 jwt.secret 保持一致 */
    @Value("${jwt.secret}")
    private String secret;

    private SecretKey getKey() {
        return Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 解析令牌，校验签名与有效期
     *
     * @param token 令牌
     * @return 载荷信息，解析失败抛出异常
     */
    public Claims parseToken(String token) {
        return Jwts.parser()
                .verifyWith(getKey())
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }
}
