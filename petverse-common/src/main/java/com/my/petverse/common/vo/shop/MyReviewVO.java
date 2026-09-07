package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 我的评价返回实体（聚合商品信息、店铺名称与回复互动数展示）
 */
@Data
public class MyReviewVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 评价ID */
    private Long reviewId;

    /** 商品ID */
    private Long productId;

    /** 商品名称（商品已删除时为空，前端降级展示） */
    private String productName;

    /** 商品主图（商品已删除时为空） */
    private String productImage;

    /** 商品类型：1-宠物用品 2-宠物食品 3-活体宠物 */
    private Integer category;

    /** 商品类型名称 */
    private String categoryName;

    /** 店铺名称 */
    private String shopName;

    /** 我的评分：1~5 星 */
    private Integer rating;

    /** 我的评价内容 */
    private String content;

    /** 评价图片OSS地址列表（最多9张） */
    private List<String> imageUrls;

    /** 评价视频OSS地址（最多1个） */
    private String videoUrl;

    /** 该评价下的回复互动数 */
    private Long replyCount;

    /** 评价时间 */
    private LocalDateTime createTime;
}
