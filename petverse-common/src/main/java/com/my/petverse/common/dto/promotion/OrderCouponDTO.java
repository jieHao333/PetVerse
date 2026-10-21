package com.my.petverse.common.dto.promotion;

import lombok.Data;

import java.io.Serializable;
import java.util.List;

/**
 * 订单商品及所选优惠券信息（按券方案计算优惠明细的入参契约）
 */
@Data
public class OrderCouponDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户优惠券 id 集合 */
    private List<Long> userCouponIds;

    /** 订单中的商品列表 */
    private List<OrderProductDTO> productList;
}
