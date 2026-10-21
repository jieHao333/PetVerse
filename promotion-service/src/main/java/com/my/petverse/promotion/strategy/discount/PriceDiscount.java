package com.my.petverse.promotion.strategy.discount;

import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.promotion.utils.PromotionUtils;

/** 满减券：满 threshold 减 value，优惠金额固定 */
public class PriceDiscount implements Discount {

    private static final String RULE_TEMPLATE = "满{}减{}";

    @Override
    public boolean canUse(int totalAmount, Coupon coupon) {
        return totalAmount >= coupon.getThresholdAmount();
    }

    @Override
    public int calculateDiscount(int totalAmount, Coupon coupon) {
        return coupon.getDiscountValue();
    }

    @Override
    public String getRule(Coupon coupon) {
        return PromotionUtils.format(
                RULE_TEMPLATE,
                PromotionUtils.scaleToStr(coupon.getThresholdAmount(), 2),
                PromotionUtils.scaleToStr(coupon.getDiscountValue(), 2)
        );
    }
}
