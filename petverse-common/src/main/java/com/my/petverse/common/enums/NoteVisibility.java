package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 笔记可见性枚举
 */
@Getter
public enum NoteVisibility {

    /** 公开：所有用户可见 */
    PUBLIC(0, "公开"),

    /** 仅好友：作者的好友可见（作者本人始终可见） */
    FRIENDS_ONLY(1, "仅好友"),

    /** 仅自己：只有作者本人可见 */
    PRIVATE(2, "仅自己");

    /** 存储值 */
    private final int code;

    /** 展示文案 */
    private final String label;

    NoteVisibility(int code, String label) {
        this.code = code;
        this.label = label;
    }

    /** 按存储值解析，非法值按公开处理 */
    public static NoteVisibility of(Integer code) {
        if (code == null) {
            return PUBLIC;
        }
        for (NoteVisibility v : values()) {
            if (v.code == code) {
                return v;
            }
        }
        return PUBLIC;
    }
}
