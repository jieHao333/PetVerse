package com.my.petverse.common.enums;

import com.baomidou.mybatisplus.annotation.EnumValue;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import lombok.Getter;

/**
 * 兑换码状态枚举
 */
@Getter
public enum ExchangeCodeStatus {

    /** 待兑换 */
    UNUSED(1, "待兑换"),
    /** 已兑换 */
    USED(2, "已兑换"),
    /** 兑换活动已结束 */
    EXPIRED(3, "兑换活动已结束");

    /** 状态编码，对应数据库字段 */
    @EnumValue
    @JsonValue
    private final int value;

    /** 状态名称 */
    private final String desc;

    ExchangeCodeStatus(int value, String desc) {
        this.value = value;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码返回 null */
    @JsonCreator(mode = JsonCreator.Mode.DELEGATING)
    public static ExchangeCodeStatus of(Integer value) {
        if (value == null) {
            return null;
        }
        for (ExchangeCodeStatus status : values()) {
            if (status.value == value) {
                return status;
            }
        }
        return null;
    }

    /** 根据编码获取状态名称，未知编码返回空串 */
    public static String desc(Integer value) {
        ExchangeCodeStatus status = of(value);
        return status == null ? "" : status.desc;
    }
}
