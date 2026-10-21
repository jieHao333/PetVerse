package com.my.petverse.promotion.strategy.scope;

import com.my.petverse.common.dto.promotion.OrderProductDTO;
import com.my.petverse.promotion.constants.ScopeType;

import java.util.List;

/**
 * 优惠券作用范围策略接口：判断订单商品是否在券的可用范围内
 */
public interface Scope {

    boolean canUse(OrderProductDTO product);

    ScopeType getType();

    List<Long> getScopeIds();
}
