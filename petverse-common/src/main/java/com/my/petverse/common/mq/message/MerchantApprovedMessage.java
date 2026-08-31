package com.my.petverse.common.mq.message;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 商户入驻审批通过事件：消费方为 user-service，异步将申请人角色升级为商家
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class MerchantApprovedMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 入驻申请单ID（幂等去重键） */
    private Long applyId;

    /** 申请人用户ID */
    private Long userId;
}
