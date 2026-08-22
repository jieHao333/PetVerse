package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 宠物稀有度枚举
 */
@Getter
public enum PetRarity {

    /** 普通 */
    COMMON(1, "普通"),
    /** 稀有 */
    RARE(2, "稀有"),
    /** 传说 */
    LEGENDARY(3, "传说");

    /** 稀有度编码，对应数据库字段 */
    private final int code;

    /** 稀有度名称 */
    private final String desc;

    PetRarity(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /** 根据编码获取枚举，未知编码默认返回普通 */
    public static PetRarity of(int code) {
        for (PetRarity rarity : values()) {
            if (rarity.code == code) {
                return rarity;
            }
        }
        return COMMON;
    }
}
