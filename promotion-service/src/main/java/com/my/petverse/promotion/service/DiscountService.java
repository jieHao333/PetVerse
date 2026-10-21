package com.my.petverse.promotion.service;

import com.my.petverse.common.dto.promotion.CouponDiscountDTO;
import com.my.petverse.common.dto.promotion.OrderCouponDTO;
import com.my.petverse.common.dto.promotion.OrderProductDTO;

import java.util.List;

/**
 * 优惠方案计算服务（智能最优方案搜索）
 */
public interface DiscountService {

    /** 查询我的优惠券在当前订单商品下的所有可用组合方案，按优惠金额降序返回 */
    List<CouponDiscountDTO> findDiscountSolution(List<OrderProductDTO> orderProducts);

    /** 按指定的券方案计算订单优惠明细 */
    CouponDiscountDTO queryDiscountDetailByOrder(OrderCouponDTO orderCouponDTO);
}
