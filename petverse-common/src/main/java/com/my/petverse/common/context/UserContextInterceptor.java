package com.my.petverse.common.context;

import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.util.JwtUtil;
import io.jsonwebtoken.Claims;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * 用户身份解析拦截器
 * 优先从 Authorization: Bearer <token> 请求头解析 JWT，将用户ID与角色写入 {@link UserContext}；
 * 不信任任何客户端传入的 X-User-Id 请求头（网关已统一剥离），
 * 仅在无令牌时才回退解析该内部头，用于服务间 Feign 直连调用的身份透传；
 * 令牌缺失时不阻断（免登录接口不读取上下文），令牌无效时拒绝请求
 */
@Component
@RequiredArgsConstructor
public class UserContextInterceptor implements HandlerInterceptor {

    /** 令牌前缀 */
    private static final String BEARER_PREFIX = "Bearer ";

    /** 内部透传用户ID的请求头，仅接受来自服务间调用，外部请求由网关剥离 */
    private static final String HEADER_USER_ID = "X-User-Id";

    /** 内部透传用户角色的请求头 */
    private static final String HEADER_USER_ROLE = "X-User-Role";

    private final JwtUtil jwtUtil;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String authorization = request.getHeader("Authorization");
        if (StringUtils.hasText(authorization) && authorization.startsWith(BEARER_PREFIX)) {
            Claims claims;
            try {
                claims = jwtUtil.parseToken(authorization.substring(BEARER_PREFIX.length()).trim());
            } catch (Exception e) {
                throw new BusinessException(ResultCode.UNAUTHORIZED, "登录已失效，请重新登录");
            }
            Long userId = Long.valueOf(claims.getSubject());
            // 存量令牌可能无 role 声明，缺省按普通用户处理
            String role = claims.get("role", String.class);
            UserContext.set(userId, StringUtils.hasText(role) ? role : "USER");
            return true;
        }
        // 无令牌：回退解析服务间透传的内部头（外部请求的该头已被网关剥离）
        String internalUserId = request.getHeader(HEADER_USER_ID);
        if (StringUtils.hasText(internalUserId)) {
            try {
                String role = request.getHeader(HEADER_USER_ROLE);
                UserContext.set(Long.valueOf(internalUserId), StringUtils.hasText(role) ? role : "USER");
            } catch (NumberFormatException e) {
                throw new BusinessException(ResultCode.UNAUTHORIZED, "登录已失效，请重新登录");
            }
            return true;
        }
        // 完全无身份：免登录接口直接放行，需要身份的接口由 UserContext.getUserId() 抛 401
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                                Object handler, Exception ex) {
        // 请求结束清理，防止线程池复用导致身份串号
        UserContext.clear();
    }
}
