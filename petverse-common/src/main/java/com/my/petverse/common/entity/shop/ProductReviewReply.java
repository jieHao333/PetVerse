package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 商品评价回复实体，对应数据库表 product_review_reply
 * 所有登录用户可在评价下自由互动，支持回复某条回复（平铺展示）
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("product_review_reply")
public class ProductReviewReply extends BaseEntity {

    /** 评价ID */
    private Long reviewId;

    /** 回复人用户ID */
    private Long userId;

    /** 被回复人用户ID（回复某条回复时记录） */
    private Long replyUserId;

    /** 回复内容 */
    private String content;
}
