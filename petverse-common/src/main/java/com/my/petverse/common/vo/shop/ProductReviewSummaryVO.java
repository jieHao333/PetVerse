package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;

/**
 * 商品评价汇总返回实体（商品详情页评价区头部展示）
 */
@Data
public class ProductReviewSummaryVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品ID */
    private Long productId;

    /** 评价总数 */
    private Long totalCount;

    /** 平均评分（保留1位小数，无评价时为 0.0） */
    private BigDecimal avgRating;

    /** 当前登录用户是否可评价（存在已完成订单且从未评价过，含历史已删除记录） */
    private Boolean canReview;

    /** 当前登录用户是否已评价过该商品（未删除的评价） */
    private Boolean reviewed;

    /** 当前登录用户是否曾评价过该商品（含已删除，删除后无法重新评价） */
    private Boolean everReviewed;
}
