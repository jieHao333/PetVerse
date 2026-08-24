package com.my.petverse.common.vo.space;

import com.my.petverse.common.vo.pet.PetExpGainVO;
import lombok.Data;

import java.io.Serializable;

/**
 * 发布动态结果返回实体，携带宠物经验奖励信息
 */
@Data
public class SpaceCreateVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 新建动态ID */
    private Long spaceId;

    /** 宠物经验奖励结果，用户未领取宠物时为 null */
    private PetExpGainVO petExp;
}
