package com.my.petverse.promotion.strategy.discount;

import com.my.petverse.common.entity.promotion.Coupon;

/**
 * 优惠券折扣功能接口（策略模式）：每种折扣类型实现一套
 * 判断可用、计算优惠金额、生成规则描述
 */
public interface Discount {

    /**
     * 判断当前价格是否满足优惠券使用限制
     *
     * @param totalAmount 订单总价（分）
     * @param coupon      优惠券信息
     * @return 是否可以使用优惠券
     */
    boolean canUse(int totalAmount, Coupon coupon);

    /**
     * 计算折扣金额
     *
     * @param totalAmount 总金额（分）
     * @param coupon      优惠券信息
     * @return 折扣金额（分）
     */
    int calculateDiscount(int totalAmount, Coupon coupon);

    /**
     * 根据优惠券规则返回规则描述信息
     */
    String getRule(Coupon coupon);
}
