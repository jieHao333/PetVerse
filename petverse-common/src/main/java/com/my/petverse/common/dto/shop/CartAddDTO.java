package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 加入购物车参数
 */
@Data
public class CartAddDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品ID */
    @NotNull(message = "商品ID不能为空")
    private Long productId;

    /** 购买数量 */
    @NotNull(message = "购买数量不能为空")
    @Min(value = 1, message = "购买数量不能小于1")
    @Max(value = 999, message = "购买数量不能大于999")
    private Integer quantity;
}
