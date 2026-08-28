package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 订单取货方式枚举（目前仅支持到店自取，预留外卖配送扩展）
 */
@Getter
public enum PickupType {

    /** 到店自取 */
    SELF_PICKUP(1, "到店自取"),
    /** 外卖配送（暂未开放） */
    DELIVERY(2, "外卖配送");

    /** 方式编码，对应数据库字段 */
    private final int code;

    /** 方式名称 */
    private final String desc;

    PickupType(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码返回 null */
    public static PickupType of(Integer code) {
        if (code == null) {
            return null;
        }
        for (PickupType type : values()) {
            if (type.code == code) {
                return type;
            }
        }
        return null;
    }
}
