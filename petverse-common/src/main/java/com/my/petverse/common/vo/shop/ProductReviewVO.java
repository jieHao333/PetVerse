package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 商品评价返回实体（聚合评价人昵称与头像展示）
 */
@Data
public class ProductReviewVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 评价ID */
    private Long id;

    /** 商品ID */
    private Long productId;

    /** 评价人用户ID */
    private Long userId;

    /** 评价人昵称（用户服务不可用时降级为空） */
    private String userNickname;

    /** 评价人头像（用户服务不可用时降级为空） */
    private String userAvatar;

    /** 评分：1~5 星 */
    private Integer rating;

    /** 评价内容 */
    private String content;

    /** 评价图片OSS地址列表（最多9张） */
    private List<String> imageUrls;

    /** 评价视频OSS地址（最多1个） */
    private String videoUrl;

    /** 评价时间 */
    private LocalDateTime createTime;
}
