package com.my.petverse.common.context;

import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;

/**
 * 当前登录用户上下文，基于 ThreadLocal 在请求线程内共享用户身份
 * 由 UserContextInterceptor 解析 JWT 令牌后写入，请求结束时清理；
 * 业务代码统一通过此类获取当前用户，禁止再从请求头/请求参数接收用户ID
 */
public final class UserContext {

    /** 当前用户ID */
    private static final ThreadLocal<Long> USER_ID = new ThreadLocal<>();

    /** 当前用户角色（USER/MERCHANT/ADMIN） */
    private static final ThreadLocal<String> USER_ROLE = new ThreadLocal<>();

    private UserContext() {
    }

    /** 写入当前用户身份 */
    public static void set(Long userId, String role) {
        USER_ID.set(userId);
        USER_ROLE.set(role);
    }

    /** 获取当前用户ID，未登录时抛出 401 业务异常 */
    public static Long getUserId() {
        Long userId = USER_ID.get();
        if (userId == null) {
            throw new BusinessException(ResultCode.UNAUTHORIZED, "未登录或登录已失效");
        }
        return userId;
    }

    /** 获取当前用户角色，未登录返回 null */
    public static String getRole() {
        return USER_ROLE.get();
    }

    /** 获取当前用户ID，未登录返回 null（供定时任务、Feign 转发等非强登录场景使用） */
    public static Long peekUserId() {
        return USER_ID.get();
    }

    /** 清理上下文，必须在请求结束时调用，防止线程池复用导致身份串号 */
    public static void clear() {
        USER_ID.remove();
        USER_ROLE.remove();
    }
}
