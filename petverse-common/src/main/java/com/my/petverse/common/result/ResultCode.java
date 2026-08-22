package com.my.petverse.common.result;

import lombok.Getter;

/**
 * 统一返回状态码枚举
 */
@Getter
public enum ResultCode {

    /** 操作成功 */
    SUCCESS(200, "操作成功"),
    /** 请求参数错误 */
    BAD_REQUEST(400, "请求参数错误"),
    /** 未授权 */
    UNAUTHORIZED(401, "未授权"),
    /** 禁止访问 */
    FORBIDDEN(403, "禁止访问"),
    /** 资源不存在 */
    NOT_FOUND(404, "资源不存在"),
    /** 服务器内部错误 */
    INTERNAL_ERROR(500, "服务器内部错误");

    /** 状态码 */
    private final int code;

    /** 提示信息 */
    private final String msg;

    ResultCode(int code, String message) {
        this.code = code;
        this.msg = message;
    }
}
