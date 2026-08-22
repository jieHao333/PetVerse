package com.my.petverse.common.dto.pet;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 宠物分页查询参数
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class PetPageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 宠物名称（模糊匹配） */
    private String name;

    /** 物种（精确匹配） */
    private String species;

    /** 所属用户ID */
    private Long userId;
}
