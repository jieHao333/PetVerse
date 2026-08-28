package com.my.petverse.common.util;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;

/**
 * JWT 工具类，负责令牌的签发与解析
 * 令牌中 subject 存放用户ID，携带 username 声明
 */
@Component
public class JwtUtil {

    /** 签名密钥，需与服务端配置 jwt.secret 保持一致 */
    @Value("${jwt.secret}")
    private String secret;

    /** 令牌有效期（秒） */
    @Value("${jwt.expire-seconds:86400}")
    private long expireSeconds;

    /**
     * 生成签名密钥
     */
    private SecretKey getKey() {
        return Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 为用户签发令牌
     *
     * @param userId   用户ID
     * @param username 用户名
     * @param role     用户角色（USER/MERCHANT/ADMIN）
     * @return JWT 字符串
     */
    public String createToken(Long userId, String username, String role) {
        Date now = new Date();
        Date expire = new Date(now.getTime() + expireSeconds * 1000);
        return Jwts.builder()
                .subject(String.valueOf(userId))
                .claim("username", username)
                .claim("role", role)
                .issuedAt(now)
                .expiration(expire)
                .signWith(getKey())
                .compact();
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

    /**
     * 从令牌中提取用户ID
     */
    public Long getUserId(String token) {
        return Long.valueOf(parseToken(token).getSubject());
    }
}
