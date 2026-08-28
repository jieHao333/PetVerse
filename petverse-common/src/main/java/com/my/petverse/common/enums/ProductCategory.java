package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 商品类型枚举
 */
@Getter
public enum ProductCategory {

    /** 宠物用品 */
    SUPPLIES(1, "宠物用品"),
    /** 宠物食品 */
    FOOD(2, "宠物食品"),
    /** 活体宠物 */
    LIVE_PET(3, "活体宠物");

    /** 类型编码，对应数据库字段 */
    private final int code;

    /** 类型名称 */
    private final String desc;

    ProductCategory(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码返回 null */
    public static ProductCategory of(Integer code) {
        if (code == null) {
            return null;
        }
        for (ProductCategory category : values()) {
            if (category.code == code) {
                return category;
            }
        }
        return null;
    }
}
