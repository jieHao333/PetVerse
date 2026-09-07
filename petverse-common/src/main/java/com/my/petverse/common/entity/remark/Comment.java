package com.my.petverse.common.entity.remark;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 通用评论实体，对应数据库表 comment
 * 与点赞同构，以 (targetType, targetId) 定位被评论对象，当前用于圈子动态评论
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("comment")
public class Comment extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 评论对象类型，见 CommentTargetType */
    private Integer targetType;

    /** 被评论对象ID */
    private Long targetId;

    /** 评论人用户ID */
    private Long userId;

    /** 被回复人用户ID，为空表示直接评论对象而非回复某条评论 */
    private Long replyUserId;

    /** 评论内容 */
    private String content;
}
