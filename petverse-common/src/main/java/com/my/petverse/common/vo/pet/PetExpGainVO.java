package com.my.petverse.common.vo.pet;

import lombok.Data;

import java.io.Serializable;

/**
 * 宠物经验获取结果返回实体
 */
@Data
public class PetExpGainVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    private Long userId;

    /** 宠物ID */
    private Long petId;

    /** 本次获得的经验值 */
    private long gainedExp;

    /** 获取经验后的宠物等级 */
    private int level;

    /** 获取经验后的当前等级经验值 */
    private long exp;

    /** 本次是否触发升级 */
    private boolean leveledUp;
}
