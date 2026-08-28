package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;

/**
 * 订单明细返回实体（下单时商品快照）
 */
@Data
public class OrderItemVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 明细ID */
    private Long id;

    /** 商品ID */
    private Long productId;

    /** 商品名称快照 */
    private String productName;

    /** 商品主图快照 */
    private String productImage;

    /** 成交单价(元) */
    private BigDecimal price;

    /** 购买数量 */
    private Integer quantity;

    /** 小计金额(元) */
    private BigDecimal amount;
}
