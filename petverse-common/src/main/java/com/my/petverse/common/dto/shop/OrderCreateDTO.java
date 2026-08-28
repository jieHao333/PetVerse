package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;
import java.util.List;

/**
 * 创建订单参数（从购物车勾选结算，一次只能结算同一店铺的商品）
 */
@Data
public class OrderCreateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 勾选结算的购物车条目ID列表 */
    @NotEmpty(message = "请选择要结算的商品")
    private List<Long> cartItemIds;

    /** 买家备注 */
    @Size(max = 200, message = "备注不能超过200字")
    private String remark;
}
