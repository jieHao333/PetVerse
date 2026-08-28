package com.my.petverse.common.enums;

import lombok.Getter;

/**
 * 用户角色枚举
 */
@Getter
public enum UserRole {

    /** 普通用户 */
    USER("普通用户"),
    /** 商家 */
    MERCHANT("商家"),
    /** 管理员 */
    ADMIN("管理员");

    /** 角色名称 */
    private final String desc;

    UserRole(String desc) {
        this.desc = desc;
    }

    /** 根据角色编码获取枚举，未知编码默认返回普通用户 */
    public static UserRole of(String name) {
        for (UserRole role : values()) {
            if (role.name().equals(name)) {
                return role;
            }
        }
        return USER;
    }
}
