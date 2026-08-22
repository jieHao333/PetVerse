package com.my.petverse.common.exception;

import com.my.petverse.common.result.ResultCode;
import lombok.Getter;

/**
 * 业务异常，携带业务状态码
 * 由 GlobalExceptionHandler 统一捕获处理
 */
@Getter
public class BusinessException extends RuntimeException {

    /** 业务状态码 */
    private final int code;

    /** 默认使用内部错误码 */
    public BusinessException(String message) {
        super(message);
        this.code = ResultCode.INTERNAL_ERROR.getCode();
    }

    /** 自定义状态码 */
    public BusinessException(int code, String message) {
        super(message);
        this.code = code;
    }

    /** 使用枚举状态码 */
    public BusinessException(ResultCode resultCode) {
        super(resultCode.getMsg());
        this.code = resultCode.getCode();
    }

    /** 使用枚举状态码并自定义提示信息 */
    public BusinessException(ResultCode resultCode, String message) {
        super(message);
        this.code = resultCode.getCode();
    }
}
