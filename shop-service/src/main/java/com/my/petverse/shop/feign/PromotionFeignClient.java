package com.my.petverse.shop.feign;

import com.my.petverse.common.dto.promotion.CouponDiscountDTO;
import com.my.petverse.common.dto.promotion.OrderCouponDTO;
import com.my.petverse.common.result.Result;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.List;

/**
 * 促销服务 Feign 客户端：下单时按所选优惠券试算抵扣金额并核销，取消订单时退还优惠券
 */
@FeignClient(name = "promotion-service")
public interface PromotionFeignClient {

    /** 按券方案计算订单优惠明细（内部接口，网关拦截外部 /internal/ 访问） */
    @PostMapping("/promotion/internal/user-coupons/discount")
    Result<CouponDiscountDTO> queryDiscountDetail(OrderCouponDTO orderCouponDTO);

    /** 核销指定优惠券（订单创建后调用，内部接口） */
    @PutMapping("/promotion/internal/user-coupons/use")
    Result<Void> writeOffCoupon(@RequestParam("couponIds") List<Long> userCouponIds);

    /** 退还指定优惠券（订单取消后调用，内部接口） */
    @PutMapping("/promotion/internal/user-coupons/refund")
    Result<Void> refundCoupon(@RequestParam("couponIds") List<Long> userCouponIds);
}
