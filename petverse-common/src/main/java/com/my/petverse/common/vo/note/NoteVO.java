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

    /** 作者用户名（聚合自用户服务，服务不可用时降级为用户+ID） */
    private String authorUsername;

    /** 作者昵称（聚合自用户服务） */
    private String authorNickname;

    /** 可见性：0-公开 1-仅好友 2-仅自己 */
    private Integer visibility;

    /** 创建时间 */
    private LocalDateTime createTime;
}
