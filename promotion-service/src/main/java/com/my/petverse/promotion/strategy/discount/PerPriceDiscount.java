package com.my.petverse.promotion.strategy.discount;

import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.promotion.utils.PromotionUtils;

/** 每满减券：每满 threshold 减 value，循环累减，优惠金额有上限 */
public class PerPriceDiscount implements Discount {

    private static final String RULE_TEMPLATE = "每满{}减{}，上限{}";

    @Override
    public boolean canUse(int totalAmount, Coupon coupon) {
        return totalAmount >= coupon.getThresholdAmount();
    }

    @Override
    public int calculateDiscount(int totalAmount, Coupon coupon) {
        int discount = 0;
        Integer thresholdAmount = coupon.getThresholdAmount();
        Integer discountValue = coupon.getDiscountValue();
        while (totalAmount >= thresholdAmount) {
            discount += discountValue;
            totalAmount -= thresholdAmount;
        }
        return Math.min(discount, coupon.getMaxDiscountAmount());
    }

    @Override
    public String getRule(Coupon coupon) {
        return PromotionUtils.format(
                RULE_TEMPLATE,
                PromotionUtils.scaleToStr(coupon.getThresholdAmount(), 2),
                PromotionUtils.scaleToStr(coupon.getDiscountValue(), 2),
                PromotionUtils.scaleToStr(coupon.getMaxDiscountAmount(), 2));
    }
}
