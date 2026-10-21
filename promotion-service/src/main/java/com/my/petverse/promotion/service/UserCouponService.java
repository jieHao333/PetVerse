package com.my.petverse.promotion.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.mq.message.UserCouponMessage;
import com.my.petverse.common.entity.promotion.UserCoupon;
import com.my.petverse.common.dto.promotion.UserCouponPageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.promotion.CouponVO;

import java.util.List;

/**
 * 用户优惠券（领取记录）服务类
 */
public interface UserCouponService extends IService<UserCoupon> {

    /** 领取优惠券（Lua 原子校验 + MQ 异步落库） */
    void receiveCoupon(Long couponId);

    /** 领券/兑换消息消费：发放数自增 + 落库用户券 + 兑换码状态更新 */
    void checkAndCreateUserCoupon(UserCouponMessage uc);

    /** 兑换码兑换优惠券 */
    void exchangeCoupon(String code);

    /** 分页查询我的优惠券 */
    PageResult<CouponVO> queryMyCouponPage(UserCouponPageQueryDTO query);

    /** 核销指定优惠券（订单使用后） */
    void writeOffCoupon(List<Long> userCouponIds);

    /** 退还指定优惠券（订单取消后） */
    void refundCoupon(List<Long> userCouponIds);

    /** 查询已用优惠券的规则描述 */
    List<String> queryDiscountRules(List<Long> userCouponIds);
}
