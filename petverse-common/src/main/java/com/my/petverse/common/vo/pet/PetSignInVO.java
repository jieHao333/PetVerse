package com.my.petverse.common.vo.pet;

import lombok.Data;

import java.io.Serializable;
import java.util.List;

/**
 * 宠物签到结果返回实体
 * 签到为用户名下所有宠物统一发放经验
 */
@Data
public class PetSignInVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    private Long userId;

    /** 本次签到每只宠物获得的经验值 */
    private long gainedExp;

    /** 连续签到天数 */
    private int signStreak;

    /** 每只宠物的签到结果 */
    private List<Item> pets;

    /** 单只宠物签到结果明细 */
    @Data
    public static class Item implements Serializable {

        private static final long serialVersionUID = 1L;

        /** 宠物ID */
        private Long petId;

        /** 宠物名称 */
        private String name;

        /** 签到后的等级 */
        private int level;

        /** 签到后的当前等级经验值 */
        private long exp;

        /** 本次签到是否触发升级 */
        private boolean leveledUp;
    }
}
