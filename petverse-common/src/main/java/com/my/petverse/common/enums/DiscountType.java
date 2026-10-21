package com.my.petverse.common.enums;

import com.baomidou.mybatisplus.annotation.EnumValue;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import lombok.Getter;

/**
 * 优惠券折扣类型枚举
 */
@Getter
public enum DiscountType {

    /** 每满减：每满 threshold 减 value，上限 maxDiscount */
    PER_PRICE_DISCOUNT(1, "每满减"),
    /** 折扣：满 threshold 打 value 折（80 表示 8 折），上限 maxDiscount */
    RATE_DISCOUNT(2, "折扣"),
    /** 无门槛：直接抵扣 value */
    NO_THRESHOLD(3, "无门槛"),
    /** 满减：满 threshold 减 value */
    PRICE_DISCOUNT(4, "满减");

    /** 类型编码，对应数据库字段 */
    @EnumValue
    @JsonValue
    private final int value;

    /** 类型名称 */
    private final String desc;

    DiscountType(int value, String desc) {
        this.value = value;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码返回 null */
    @JsonCreator(mode = JsonCreator.Mode.DELEGATING)
    public static DiscountType of(Integer value) {
        if (value == null) {
            return null;
        }
        for (DiscountType type : values()) {
            if (type.value == value) {
                return type;
            }
        }
        return null;
    }
}
