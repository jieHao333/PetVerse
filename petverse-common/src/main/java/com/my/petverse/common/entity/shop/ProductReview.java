package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 商品评价实体，对应数据库表 product_review
 * 仅已完成订单的买家可评价，同一用户对同一商品仅能评价一次
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("product_review")
public class ProductReview extends BaseEntity {

    /** 商品ID */
    private Long productId;

    /** 商家ID（冗余，支持按店铺维度统计） */
    private Long merchantId;

    /** 评价人用户ID */
    private Long userId;

    /** 来源订单ID（已完成订单，评价资格凭证） */
    private Long orderId;

    /** 评分：1~5 星 */
    private Integer rating;

    /** 评价内容 */
    private String content;

    /** 评价图片OSS地址，逗号分隔（最多9张） */
    private String imageUrls;

    /** 评价视频OSS地址（最多1个） */
    private String videoUrl;
}
