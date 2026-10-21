package com.my.petverse.promotion.strategy.discount;

import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.promotion.utils.PromotionUtils;

/** 折扣券：满 threshold 打 discountValue 折（80 表示 8 折），优惠金额有上限 */
public class RateDiscount implements Discount {

    private static final String RULE_TEMPLATE = "满{}打{}折，上限{}元";

    @Override
    public boolean canUse(int totalAmount, Coupon coupon) {
        return totalAmount >= coupon.getThresholdAmount();
    }

    @Override
    public int calculateDiscount(int totalAmount, Coupon coupon) {
        // 计算折扣，扩大 100 倍计算，向下取整，单位是分
        return Math.min(coupon.getMaxDiscountAmount(), totalAmount * (100 - coupon.getDiscountValue()) / 100);
    }

    @Override
    public String getRule(Coupon coupon) {
        return PromotionUtils.format(
                RULE_TEMPLATE,
                PromotionUtils.scaleToStr(coupon.getThresholdAmount(), 2),
                PromotionUtils.scaleToStr(coupon.getDiscountValue(), 1),
                PromotionUtils.scaleToStr(coupon.getMaxDiscountAmount(), 2)
        );
    }
}
