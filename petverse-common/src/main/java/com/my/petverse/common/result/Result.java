package com.my.petverse.common.result;

import lombok.Data;
import java.io.Serializable;

/**
 * 统一接口返回结果包装类
 *
 * @param <T> 返回数据类型
 */
@Data
public class Result<T> implements Serializable {

    /** 业务状态码，200 表示成功 */
    private int code;

    /** 提示信息 */
    private String msg;

    /** 返回数据 */
    private T data;

    public Result() {
    }

    public Result(int code, String msg, T data) {
        this.code = code;
        this.msg = msg;
        this.data = data;
    }

    /** 成功，无返回数据 */
    public static <T> Result<T> success() {
        return new Result<>(ResultCode.SUCCESS.getCode(), ResultCode.SUCCESS.getMsg(), null);
    }

    /** 成功，带返回数据 */
    public static <T> Result<T> success(T data) {
        return new Result<>(ResultCode.SUCCESS.getCode(), ResultCode.SUCCESS.getMsg(), data);
    }

    /** 成功，自定义提示信息并带返回数据 */
    public static <T> Result<T> success(String msg, T data) {
        return new Result<>(ResultCode.SUCCESS.getCode(), msg, data);
    }

    /** 失败，使用默认内部错误码 */
    public static <T> Result<T> fail(String msg) {
        return new Result<>(ResultCode.INTERNAL_ERROR.getCode(), msg, null);
    }

    /** 失败，自定义状态码和提示信息 */
    public static <T> Result<T> fail(int code, String msg) {
        return new Result<>(code, msg, null);
    }

    /** 失败，使用枚举状态码 */
    public static <T> Result<T> fail(ResultCode resultCode) {
        return new Result<>(resultCode.getCode(), resultCode.getMsg(), null);
    }

    /** 失败，使用枚举状态码并自定义提示信息 */
    public static <T> Result<T> fail(ResultCode resultCode, String msg) {
        return new Result<>(resultCode.getCode(), msg, null);
    }
}
