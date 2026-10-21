package com.my.petverse.common.dto.promotion;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 订单的可用优惠券及折扣信息（智能最优方案计算的出参契约，金额单位为分）
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Accessors(chain = true)
public class CouponDiscountDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户优惠券 id 集合（方案的用券组合） */
    private List<Long> ids = new ArrayList<>();

    /** 优惠券规则描述集合（与 ids 一一对应） */
    private List<String> rules = new ArrayList<>();

    /** 本订单最大优惠金额（分） */
    private Integer discountAmount = 0;

    /** 优惠明细：key 是商品 id，value 是商品优惠金额（分） */
    private Map<Long, Integer> discountDetail;
}
