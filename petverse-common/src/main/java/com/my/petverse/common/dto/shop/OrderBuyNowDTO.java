package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 直接购买下单参数（商品详情页立即购买，不经过购物车）
 */
@Data
public class OrderBuyNowDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品ID */
    @NotNull(message = "商品ID不能为空")
    private Long productId;

    /** 购买数量 */
    @NotNull(message = "购买数量不能为空")
    @Min(value = 1, message = "购买数量不能小于1")
    @Max(value = 999, message = "购买数量不能大于999")
    private Integer quantity;

    /** 买家备注 */
    @Size(max = 200, message = "备注不能超过200字")
    private String remark;
}
