package com.my.petverse.common.dto.shop;

import com.my.petverse.common.dto.base.BasePageQuery;
import jakarta.validation.constraints.NotNull;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 评价回复分页查询参数（按时间正序，展示互动过程）
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class ProductReviewReplyPageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 评价ID */
    @NotNull(message = "评价ID不能为空")
    private Long reviewId;
}
