package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 商家入驻申请审批状态枚举
 */
@Getter
public enum MerchantApplyStatus {

    /** 待审核 */
    PENDING(0, "待审核"),
    /** 已通过 */
    APPROVED(1, "已通过"),
    /** 已驳回 */
    REJECTED(2, "已驳回");

    /** 状态编码，对应数据库字段 */
    private final int code;

    /** 状态名称 */
    private final String desc;

    MerchantApplyStatus(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码默认返回待审核 */
    public static MerchantApplyStatus of(int code) {
        for (MerchantApplyStatus status : values()) {
            if (status.code == code) {
                return status;
            }
        }
        return PENDING;
    }
}
