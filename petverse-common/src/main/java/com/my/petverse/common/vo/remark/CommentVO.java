package com.my.petverse.common.vo.remark;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 评论返回实体（聚合评论人与被回复人昵称头像展示）
 */
@Data
public class CommentVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 评论ID */
    private Long id;

    /** 评论对象类型：0-圈子动态 */
    private Integer targetType;

    /** 被评论对象ID */
    private Long targetId;

    /** 评论人用户ID */
    private Long userId;

    /** 评论人昵称（用户服务不可用时为空） */
    private String userNickname;

    /** 评论人头像（用户服务不可用时为空） */
    private String userAvatar;

    /** 被回复人用户ID，为空表示直接评论对象 */
    private Long replyUserId;

    /** 被回复人昵称 */
    private String replyUserNickname;

    /** 评论内容 */
    private String content;

    /** 评论时间 */
    private LocalDateTime createTime;
}
