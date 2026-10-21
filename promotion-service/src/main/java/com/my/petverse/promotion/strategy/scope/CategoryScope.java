package com.my.petverse.promotion.strategy.scope;

import com.my.petverse.common.dto.promotion.OrderProductDTO;
import com.my.petverse.promotion.constants.ScopeType;
import lombok.Getter;
import lombok.RequiredArgsConstructor;

import java.util.List;

/** 限定商品分类：商品的分类 id 在范围内则可用 */
@Getter
@RequiredArgsConstructor
public class CategoryScope implements Scope {

    private final List<Long> scopeIds;

    @Override
    public boolean canUse(OrderProductDTO product) {
        return scopeIds.contains(product.getCategoryId());
    }

    @Override
    public ScopeType getType() {
        return ScopeType.CATEGORY;
    }
}
