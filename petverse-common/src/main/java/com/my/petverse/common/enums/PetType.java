package com.my.petverse.common.enums;

/**
 * 宠物类型枚举
 */
public enum PetType {

    /** 真实宠物：用户登记的实际饲养宠物，纯档案，不参与等级/经验/签到 */
    REAL("真实宠物"),

    /** 虚拟宠物：图鉴抽卡领养的社交形象，保留等级/经验/签到玩法 */
    VIRTUAL("虚拟宠物");

    /** 类型中文名 */
    private final String label;

    PetType(String label) {
        this.label = label;
    }

    public String getLabel() {
        return label;
    }

    /** 按枚举名取中文名，未匹配返回 null */
    public static String labelOf(String name) {
        if (name == null) {
            return null;
        }
        for (PetType t : values()) {
            if (t.name().equals(name)) {
                return t.label;
            }
        }
        return null;
    }
}
