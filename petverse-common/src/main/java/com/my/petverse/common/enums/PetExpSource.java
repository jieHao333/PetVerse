package com.my.petverse.common.enums;

/**
 * 宠物经验获取来源枚举
 */
public enum PetExpSource {

    /** 发布宠域空间动态（常量名保留 NOTE 以兼容历史数据） */
    NOTE(10, "发布动态");

    /** 该来源一次获得的经验值 */
    private final int exp;

    /** 来源描述 */
    private final String description;

    PetExpSource(int exp, String description) {
        this.exp = exp;
        this.description = description;
    }

    public int getExp() {
        return exp;
    }

    public String getDescription() {
        return description;
    }
}
