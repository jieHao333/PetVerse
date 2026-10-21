package com.my.petverse.common.dto.promotion;

import lombok.Data;

import java.io.Serializable;

/**
 * 商品简要信息（shop-service 内部接口返回，供 promotion-service 展示优惠券限定范围的商品名称）
 */
@Data
public class ProductSimpleInfoDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品 id */
    private Long id;

    /** 商品名称 */
    private String name;

    /** 商品分类：1-宠物用品 2-宠物食品 3-活体宠物 */
    private Integer category;
}
