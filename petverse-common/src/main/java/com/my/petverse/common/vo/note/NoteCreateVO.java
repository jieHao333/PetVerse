package com.my.petverse.common.vo.note;

import com.my.petverse.common.vo.pet.PetExpGainVO;
import lombok.Data;

import java.io.Serializable;

/**
 * 新增笔记结果返回实体，携带宠物经验奖励信息
 */
@Data
public class NoteCreateVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 新建笔记ID */
    private Long noteId;

    /** 宠物经验奖励结果，用户未领取宠物时为 null */
    private PetExpGainVO petExp;
}
