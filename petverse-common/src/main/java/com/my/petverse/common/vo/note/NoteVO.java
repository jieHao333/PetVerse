package com.my.petverse.common.vo.note;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 笔记返回实体
 */
@Data
public class NoteVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 笔记ID */
    private Long id;

    /** 笔记标题 */
    private String title;

    /** 笔记内容 */
    private String content;

    /** 笔记分类 */
    private String category;

    /** 关联的宠物ID */
    private Long petId;

    /** 所属用户ID */
    private Long userId;

    /** 创建时间 */
    private LocalDateTime createTime;
}
