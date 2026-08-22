package com.my.petverse.common.entity.note;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 笔记实体，对应数据库表 note
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("note")
public class Note extends BaseEntity {

    /** 笔记标题 */
    private String title;

    /** 笔记内容 */
    private String content;

    /** 笔记分类 */
    private String category;

    /** 关联的宠物ID，可为空 */
    private Long petId;

    /** 所属用户ID */
    private Long userId;
}
