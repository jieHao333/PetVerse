package com.my.petverse.promotion.constants;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.my.petverse.promotion.strategy.scope.CategoryScope;
import com.my.petverse.promotion.strategy.scope.NoScope;
import com.my.petverse.promotion.strategy.scope.ProductScope;
import com.my.petverse.promotion.strategy.scope.Scope;
import lombok.AllArgsConstructor;
import lombok.Getter;

import java.util.List;

/**
 * 优惠券作用范围类型枚举，按类型构建对应的范围策略对象
 */
@Getter
@AllArgsConstructor
@JsonFormat(shape = JsonFormat.Shape.OBJECT)
public enum ScopeType {

    /** 全部商品 */
    ALL(0, "全部") {
        @Override
        public Scope buildScope(List<Long> scopeIds) {
            return new NoScope();
        }
    },
    /** 指定商品分类 */
    CATEGORY(1, "指定分类") {
        @Override
        public Scope buildScope(List<Long> scopeIds) {
            return new CategoryScope(scopeIds);
        }
    },
    /** 指定商品 */
    PRODUCT(2, "指定商品") {
        @Override
        public Scope buildScope(List<Long> scopeIds) {
            return new ProductScope(scopeIds);
        }
    };

    public static final String CATEGORY_HANDLER_NAME = "CATEGORY";
    public static final String PRODUCT_HANDLER_NAME = "PRODUCT";

    /** 类型编码 */
    private final int value;

    /** 类型名称 */
    private final String desc;

    @JsonCreator
    public static ScopeType of(Integer value) {
        if (value == null) {
            return null;
        }
        for (ScopeType type : values()) {
            if (type.value == value) {
                return type;
            }
        }
        return null;
    }

    public static String desc(Integer value) {
        ScopeType type = of(value);
        return type == null ? "" : type.desc;
    }

    public abstract Scope buildScope(List<Long> scopeIds);
}
