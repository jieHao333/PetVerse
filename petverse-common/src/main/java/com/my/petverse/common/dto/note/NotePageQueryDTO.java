package com.my.petverse.common.dto.note;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 笔记分页查询参数
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class NotePageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 笔记标题（模糊匹配） */
    private String title;

    /** 笔记分类（精确匹配） */
    private String category;

    /** 关联的宠物ID */
    private Long petId;

    /** 所属用户ID */
    private Long userId;
}
