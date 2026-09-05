package com.my.petverse.common.enums;

/**
 * 宠物性别枚举（真实宠物档案用）
 */
public enum PetGender {

    /** 弟弟（公） */
    MALE(1, "弟弟"),

    /** 妹妹（母） */
    FEMALE(2, "妹妹");

    /** 性别编码，持久化到 pet.gender */
    private final int code;

    /** 性别中文名 */
    private final String label;

    PetGender(int code, String label) {
        this.code = code;
        this.label = label;
    }

    public int getCode() {
        return code;
    }

    public String getLabel() {
        return label;
    }

    /** 按编码取中文名，未匹配返回 null */
    public static String labelOf(Integer code) {
        if (code == null) {
            return null;
        }
        for (PetGender g : values()) {
            if (g.code == code) {
                return g.label;
            }
        }
        return null;
    }
}
