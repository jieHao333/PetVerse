package com.my.petverse.common.mq.message;

import lombok.Data;

import java.io.Serializable;

/**
 * 领券/兑换消息体：领券成功（Lua 校验通过）后发出，消费方异步落库，
 * 实现“Redis 原子校验 + MQ 异步写库”的削峰与最终一致
 */
@Data
public class UserCouponMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户 id */
    private Long userId;

    /** 优惠券 id */
    private Long couponId;

    /** 兑换码序列号（id），手动领取时为 null */
    private Integer serialNum;
}
