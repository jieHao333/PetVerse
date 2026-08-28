package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.math.BigDecimal;

/**
 * 商品实体，对应数据库表 product（支持宠物用品/食品与活体宠物）
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("product")
public class Product extends BaseEntity {

    /** 所属商家ID */
    private Long merchantId;

    /** 商品名称 */
    private String name;

    /** 商品类型：1-宠物用品 2-宠物食品 3-活体宠物 */
    private Integer category;

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
}
