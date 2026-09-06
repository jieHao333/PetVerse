package com.my.petverse.common.dto.shop;

import com.my.petverse.common.dto.base.BasePageQuery;
import jakarta.validation.constraints.NotNull;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 商品评价分页查询参数（支持按星级筛选）
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class ProductReviewPageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 商品ID */
    @NotNull(message = "商品ID不能为空")
    private Long productId;

    /** 评分筛选：1~5 星，为空查全部 */
    private Integer rating;
}
