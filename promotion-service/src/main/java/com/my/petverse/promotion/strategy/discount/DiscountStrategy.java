package com.my.petverse.promotion.strategy.discount;

import com.my.petverse.common.enums.DiscountType;

import java.util.EnumMap;

/**
 * 折扣策略路由：按折扣类型获取对应策略实现
 */
public class DiscountStrategy {

    private static final EnumMap<DiscountType, Discount> STRATEGIES;

    static {
        STRATEGIES = new EnumMap<>(DiscountType.class);
        STRATEGIES.put(DiscountType.NO_THRESHOLD, new NoThresholdDiscount());
        STRATEGIES.put(DiscountType.PER_PRICE_DISCOUNT, new PerPriceDiscount());
        STRATEGIES.put(DiscountType.RATE_DISCOUNT, new RateDiscount());
        STRATEGIES.put(DiscountType.PRICE_DISCOUNT, new PriceDiscount());
    }

    private DiscountStrategy() {
    }

    public static Discount getDiscount(DiscountType type) {
        return STRATEGIES.get(type);
    }
}
