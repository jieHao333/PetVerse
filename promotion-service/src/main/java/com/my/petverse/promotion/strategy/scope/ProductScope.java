package com.my.petverse.promotion.strategy.scope;

import com.my.petverse.common.dto.promotion.OrderProductDTO;
import com.my.petverse.promotion.constants.ScopeType;
import lombok.Getter;
import lombok.RequiredArgsConstructor;

import java.util.List;

/** 限定指定商品：商品 id 在范围内则可用 */
@Getter
@RequiredArgsConstructor
public class ProductScope implements Scope {

    private final List<Long> scopeIds;

    @Override
    public boolean canUse(OrderProductDTO product) {
        return scopeIds.contains(product.getId());
    }

    @Override
    public ScopeType getType() {
        return ScopeType.PRODUCT;
    }
}
