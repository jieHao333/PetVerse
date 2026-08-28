package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 订单状态枚举
 */
@Getter
public enum OrderStatus {

    /** 待支付 */
    PENDING_PAYMENT(0, "待支付"),
    /** 已支付，等待买家到店取货 */
    PENDING_PICKUP(1, "待取货"),
    /** 已完成（到店核销） */
    COMPLETED(2, "已完成"),
    /** 已取消 */
    CANCELLED(3, "已取消");

    /** 状态编码，对应数据库字段 */
    private final int code;

    /** 状态名称 */
    private final String desc;

    OrderStatus(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码返回 null */
    public static OrderStatus of(Integer code) {
        if (code == null) {
            return null;
        }
        for (OrderStatus status : values()) {
            if (status.code == code) {
                return status;
            }
        }
        return null;
    }
}
