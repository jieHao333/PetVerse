package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 站内通知类型枚举，扩展新的通知场景时在此追加
 */
@Getter
public enum NotificationType {

    /** 有人评论了我的内容 */
    COMMENT(1, "评论"),

    /** 有人回复了我的评论/评价 */
    REPLY(2, "回复"),

    /** 有人点赞了我的内容 */
    LIKE(3, "点赞");

    /** 存储值 */
    private final int code;

    /** 展示文案 */
    private final String desc;

    NotificationType(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /** 按存储值解析，非法值返回 null */
    public static NotificationType of(Integer code) {
        if (code == null) {
            return null;
        }
        for (NotificationType v : values()) {
            if (v.code == code) {
                return v;
            }
        }
        return null;
    }
}
