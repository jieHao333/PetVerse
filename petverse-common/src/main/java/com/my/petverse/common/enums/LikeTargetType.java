package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 点赞对象类型枚举，通用点赞模型，扩展新对象时在此追加
 */
@Getter
public enum LikeTargetType {

    /** 宠域空间动态 */
    SPACE(0, "动态");

    /** 存储值 */
    private final int code;

    /** 展示文案 */
    private final String label;

    LikeTargetType(int code, String label) {
        this.code = code;
        this.label = label;
    }

    /** 按存储值解析，非法值返回 null */
    public static LikeTargetType of(Integer code) {
        if (code == null) {
            return null;
        }
        for (LikeTargetType v : values()) {
            if (v.code == code) {
                return v;
            }
        }
        return null;
    }
}
