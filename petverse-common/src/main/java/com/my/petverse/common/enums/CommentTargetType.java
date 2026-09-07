package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 评论对象类型枚举，通用评论模型，扩展新对象时在此追加
 */
@Getter
public enum CommentTargetType {

    /** 圈子动态 */
    SPACE(0, "动态");

    /** 存储值 */
    private final int code;

    /** 展示文案 */
    private final String label;

    CommentTargetType(int code, String label) {
        this.code = code;
        this.label = label;
    }

    /** 按存储值解析，非法值返回 null */
    public static CommentTargetType of(Integer code) {
        if (code == null) {
            return null;
        }
        for (CommentTargetType v : values()) {
            if (v.code == code) {
                return v;
            }
        }
        return null;
    }
}
