package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 发表评价回复参数（所有登录用户可在评价下自由互动）
 */
@Data
public class ProductReviewReplySaveDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 评价ID */
    @NotNull(message = "评价ID不能为空")
    private Long reviewId;

    /** 回复内容 */
    @NotBlank(message = "回复内容不能为空")
    @Size(max = 500, message = "回复内容不能超过500字")
    private String content;

    /** 被回复人用户ID（回复某条回复时传，选填） */
    private Long replyUserId;
}
