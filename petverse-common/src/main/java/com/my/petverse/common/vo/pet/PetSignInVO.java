package com.my.petverse.common.vo.pet;

import lombok.Data;

import java.io.Serializable;

/**
 * 宠物签到结果返回实体
 */
@Data
public class PetSignInVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    private Long userId;

    /** 宠物ID */
    private Long petId;

    /** 本次签到获得的经验值 */
    private long gainedExp;

    /** 连续签到天数 */
    private int signStreak;

    /** 签到后的宠物等级 */
    private int level;

    /** 签到后的当前等级经验值 */
    private long exp;

    /** 本次签到是否触发升级 */
    private boolean leveledUp;
}
