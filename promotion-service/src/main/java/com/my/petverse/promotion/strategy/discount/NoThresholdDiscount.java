package com.my.petverse.promotion.strategy.discount;

import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.promotion.utils.PromotionUtils;

/** 无门槛券：订单金额大于抵扣额即可用，优惠金额固定 */
public class NoThresholdDiscount implements Discount {

    private static final String RULE_TEMPLATE = "无门槛抵{}元";

    @Override
    public boolean canUse(int totalAmount, Coupon coupon) {
        return totalAmount > coupon.getDiscountValue();
    }

    @Override
    public int calculateDiscount(int totalAmount, Coupon coupon) {
        return coupon.getDiscountValue();
    }

    @Override
    public String getRule(Coupon coupon) {
        return PromotionUtils.format(RULE_TEMPLATE, PromotionUtils.scaleToStr(coupon.getDiscountValue(), 2));
    }
}
