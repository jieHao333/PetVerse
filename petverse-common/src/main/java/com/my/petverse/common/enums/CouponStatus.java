package com.my.petverse.common.enums;

import com.baomidou.mybatisplus.annotation.EnumValue;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import lombok.Getter;

/**
 * 优惠券状态枚举（发放生命周期）
 */
@Getter
public enum CouponStatus {

    /** 待发放（草稿，尚未开始发放） */
    DRAFT(1, "待发放"),
    /** 未开始（已定发放时间，等待到点自动开始） */
    UN_ISSUE(2, "未开始"),
    /** 发放中 */
    ISSUING(3, "发放中"),
    /** 发放结束 */
    FINISHED(4, "发放结束"),
    /** 暂停发放 */
    PAUSE(5, "暂停");

    /** 状态编码，对应数据库字段 */
    @EnumValue
    @JsonValue
    private final int value;

    /** 状态名称 */
    private final String desc;

    CouponStatus(int value, String desc) {
        this.value = value;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码返回 null */
    @JsonCreator(mode = JsonCreator.Mode.DELEGATING)
    public static CouponStatus of(Integer value) {
        if (value == null) {
            return null;
        }
        for (CouponStatus status : values()) {
            if (status.value == value) {
                return status;
            }
        }
        return null;
    }

    /** 根据编码获取状态名称，未知编码返回空串 */
    public static String desc(Integer value) {
        CouponStatus status = of(value);
        return status == null ? "" : status.desc;
    }
}
