package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 修改购物车条目数量参数
 */
@Data
public class CartUpdateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 购物车条目ID */
    @NotNull(message = "购物车条目ID不能为空")
    private Long id;

    /** 购买数量 */
    @NotNull(message = "购买数量不能为空")
    @Min(value = 1, message = "购买数量不能小于1")
    @Max(value = 999, message = "购买数量不能大于999")
    private Integer quantity;
}
