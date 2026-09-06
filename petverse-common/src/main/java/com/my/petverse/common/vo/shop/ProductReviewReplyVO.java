package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 评价回复返回实体（聚合回复人与被回复人昵称头像展示）
 */
@Data
public class ProductReviewReplyVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 回复ID */
    private Long id;

    /** 评价ID */
    private Long reviewId;

    /** 回复人用户ID */
    private Long userId;

    /** 回复人昵称（用户服务不可用时降级为空） */
    private String userNickname;

    /** 回复人头像（用户服务不可用时降级为空） */
    private String userAvatar;

    /** 被回复人用户ID（回复某条回复时返回，前端展示"回复 @昵称"） */
    private Long replyUserId;

    /** 被回复人昵称 */
    private String replyUserNickname;

    /** 回复内容 */
    private String content;

    /** 回复时间 */
    private LocalDateTime createTime;
}
