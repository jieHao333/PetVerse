package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 购物车条目实体，对应数据库表 cart_item（同一商品重复加购时累加数量）
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("cart_item")
public class CartItem extends BaseEntity {

    /** 买家用户ID */
    private Long userId;

    /** 商品ID */
    private Long productId;

    /** 购买数量 */
    private Integer quantity;
}
