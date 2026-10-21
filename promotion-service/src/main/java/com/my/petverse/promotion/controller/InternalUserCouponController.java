package com.my.petverse.promotion.controller;

import com.my.petverse.common.dto.promotion.CouponDiscountDTO;
import com.my.petverse.common.dto.promotion.OrderCouponDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.promotion.service.DiscountService;
import com.my.petverse.promotion.service.UserCouponService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 优惠券内部接口控制器：供 shop-service 下单试算、核销、退券调用。
 * 网关会拦截外部对 /internal/ 路径的访问，仅允许服务间调用
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/promotion/internal/user-coupons")
public class InternalUserCouponController {

    private final UserCouponService userCouponService;

    private final DiscountService discountService;

    /** 按券方案计算订单优惠明细 */
    @PostMapping("/discount")
    public Result<CouponDiscountDTO> queryDiscountDetailByOrder(@RequestBody OrderCouponDTO orderCouponDTO) {
        return Result.success(discountService.queryDiscountDetailByOrder(orderCouponDTO));
    }

    /** 核销指定优惠券（订单创建后调用） */
    @PutMapping("/use")
    public Result<Void> writeOffCoupon(@RequestParam("couponIds") List<Long> userCouponIds) {
        userCouponService.writeOffCoupon(userCouponIds);
        return Result.success();
    }

    /** 退还指定优惠券（订单取消后调用） */
    @PutMapping("/refund")
    public Result<Void> refundCoupon(@RequestParam("couponIds") List<Long> userCouponIds) {
        userCouponService.refundCoupon(userCouponIds);
        return Result.success();
    }
}
