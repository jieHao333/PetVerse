package com.my.petverse.common.vo.promotion;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 优惠券使用范围条目
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class CouponScopeVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 范围业务 id（分类 id 或商品 id） */
    private Long id;

    /** 范围名称（分类名或商品名） */
    private String name;
}
