package com.my.petverse.common.enums;

import com.baomidou.mybatisplus.annotation.EnumValue;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import lombok.Getter;

/**
 * 优惠券获取方式枚举
 */
@Getter
public enum ObtainType {

    /** 手动领取（发放中的券用户主动领取） */
    PUBLIC(1, "手动领取"),
    /** 发放兑换码（线下/活动渠道发放兑换码，用户凭码兑换） */
    ISSUE(2, "发放兑换码");

    /** 方式编码，对应数据库字段 */
    @EnumValue
    @JsonValue
    private final int value;

    /** 方式名称 */
    private final String desc;

    ObtainType(int value, String desc) {
        this.value = value;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码返回 null */
    @JsonCreator(mode = JsonCreator.Mode.DELEGATING)
    public static ObtainType of(Integer value) {
        if (value == null) {
            return null;
        }
        for (ObtainType type : values()) {
            if (type.value == value) {
                return type;
            }
        }
        return null;
    }
}
