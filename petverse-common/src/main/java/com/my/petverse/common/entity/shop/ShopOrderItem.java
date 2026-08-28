package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.math.BigDecimal;

/**
 * 订单明细实体，对应数据库表 shop_order_item（下单时商品快照，不随商品修改变化）
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("shop_order_item")
public class ShopOrderItem extends BaseEntity {

    /** 订单ID */
    private Long orderId;

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
