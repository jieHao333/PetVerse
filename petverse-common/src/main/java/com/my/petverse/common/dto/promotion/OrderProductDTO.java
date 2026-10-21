package com.my.petverse.common.dto.promotion;

import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;

/**
 * 订单中的商品信息（优惠计算的入参契约，shop-service 下单时组装并传给 promotion-service）
 * 金额单位为分
 */
@Data
@Accessors(chain = true)
public class OrderProductDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品 id */
    private Long id;

    /** 商品分类 id（对应 product.category） */
    private Long categoryId;

    /** 商品价格（分） */
    private Integer price;
}
