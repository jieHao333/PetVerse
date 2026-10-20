package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;

/**
 * 购物车条目返回实体（附商品与店铺信息，前端按店铺分组展示）
 */
@Data
public class CartItemVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 购物车条目ID */
    private Long id;

    /** 商品ID */
    private Long productId;

    /** 所属商家ID */
    private Long merchantId;

    /** 所属店铺名称 */
    private String shopName;

    /** 商品名称 */
    private String productName;

    /** 商品主图 */
    private String productImage;

    /** 商品单价(元) */
    private BigDecimal price;

    /** 商品库存 */
    private Integer stock;

    /** 购买数量 */
    private Integer quantity;

    /** 所属店铺状态：1-营业 0-打烊 2-封禁 */
    private Integer shopStatus;

    /** 所属店铺撤销状态：0-正常 1-冷静期 */
    private Integer shopWithdrawStatus;

    /** 所属店铺当前是否可对外售卖 */
    private Boolean shopSelling;

    /** 商品是否有效（上架且店铺可售，失效商品不可结算） */
    private Boolean valid;

    /** 失效原因（有效时为空） */
    private String invalidReason;
}
