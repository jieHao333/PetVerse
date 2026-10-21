package com.my.petverse.promotion.strategy.scope;

import java.util.List;

/**
 * 范围名称处理器接口：将范围业务 id 转换为可读名称（分类名/商品名）
 */
public interface ScopeNameHandler {

    List<String> getNameByIds(List<Long> scopeIds);
}
