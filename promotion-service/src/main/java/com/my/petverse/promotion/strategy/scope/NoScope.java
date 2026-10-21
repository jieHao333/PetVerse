package com.my.petverse.promotion.strategy.scope;

import com.my.petverse.common.dto.promotion.OrderProductDTO;
import com.my.petverse.promotion.constants.ScopeType;

import java.util.List;

/** 不限范围：所有商品可用 */
public class NoScope implements Scope {

    @Override
    public boolean canUse(OrderProductDTO product) {
        return true;
    }

    @Override
    public ScopeType getType() {
        return ScopeType.ALL;
    }

    @Override
    public List<Long> getScopeIds() {
        return null;
    }
}
