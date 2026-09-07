package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 站内通知来源枚举，标记消息来自哪个业务模块，前端据此展示来源标签与跳转目标
 */
@Getter
public enum NotificationSource {

    /** 圈子动态（动态评论回复、动态点赞） */
    SPACE(1, "圈子动态"),

    /** 商品评价（评价回复） */
    SHOP_REVIEW(2, "商品评价");

    /** 存储值 */
    private final int code;

    /** 展示文案 */
    private final String desc;

    NotificationSource(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /** 按存储值解析，非法值返回 null */
    public static NotificationSource of(Integer code) {
        if (code == null) {
            return null;
        }
        for (NotificationSource v : values()) {
            if (v.code == code) {
                return v;
            }
        }
        return null;
    }
}
