package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 商品返回实体
 */
@Data
public class ProductVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品ID */
    private Long id;

    /** 所属商家ID */
    private Long merchantId;

    /** 所属店铺名称（商城列表聚合展示） */
    private String shopName;

    /** 所属店铺状态：1-营业 0-打烊 2-封禁 */
    private Integer shopStatus;

    /** 所属店铺撤销状态：0-正常 1-冷静期 */
    private Integer shopWithdrawStatus;

    /** 所属店铺当前是否可对外售卖 */
    private Boolean shopSelling;

    /** 商品名称 */
    private String name;

    /** 商品类型：1-宠物用品 2-宠物食品 3-活体宠物 */
    private Integer category;

    /** 商品类型名称 */
    private String categoryName;

    /** 售价(元) */
    private BigDecimal price;

    /** 库存 */
    private Integer stock;

    /** 商品主图 */
    private String imageUrl;

    /** 商品详情 */
    private String description;

    /** 状态：0-下架 1-上架 */
    private Integer status;

    /** 上架时间 */
    private LocalDateTime createTime;
}
