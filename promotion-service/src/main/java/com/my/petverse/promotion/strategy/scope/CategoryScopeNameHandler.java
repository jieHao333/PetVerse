package com.my.petverse.promotion.strategy.scope;

import com.my.petverse.common.enums.ProductCategory;
import com.my.petverse.promotion.constants.ScopeType;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.stream.Collectors;

/**
 * 商品分类范围名称处理器：分类为系统内置枚举（宠物用品/宠物食品/活体宠物），本地直接转换
 */
@Component(ScopeType.CATEGORY_HANDLER_NAME)
public class CategoryScopeNameHandler implements ScopeNameHandler {

    @Override
    public List<String> getNameByIds(List<Long> scopeIds) {
        return scopeIds.stream()
                .map(id -> ProductCategory.of(id.intValue()))
                .filter(c -> c != null)
                .map(ProductCategory::getDesc)
                .collect(Collectors.toList());
    }
}
