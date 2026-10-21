package com.my.petverse.common.enums;

import com.baomidou.mybatisplus.annotation.EnumValue;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import lombok.Getter;

/**
 * 用户优惠券状态枚举
 */
@Getter
public enum UserCouponStatus {

    /** 未使用 */
    UNUSED(1, "未使用"),
    /** 已使用（已核销） */
    USED(2, "已使用"),
    /** 已过期 */
    EXPIRED(3, "已过期");

    /** 状态编码，对应数据库字段 */
    @EnumValue
    @JsonValue
    private final int value;

    /** 状态名称 */
    private final String desc;

    UserCouponStatus(int value, String desc) {
        this.value = value;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码返回 null */
    @JsonCreator(mode = JsonCreator.Mode.DELEGATING)
    public static UserCouponStatus of(Integer value) {
        if (value == null) {
            return null;
        }
        for (UserCouponStatus status : values()) {
            if (status.value == value) {
                return status;
            }
        }
        return null;
    }

    /** 根据编码获取状态名称，未知编码返回空串 */
    public static String desc(Integer value) {
        UserCouponStatus status = of(value);
        return status == null ? "" : status.desc;
    }
}
