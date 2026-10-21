package com.my.petverse.promotion.controller;

import com.my.petverse.common.dto.promotion.CouponDiscountDTO;
import com.my.petverse.common.dto.promotion.OrderProductDTO;
import com.my.petverse.common.dto.promotion.UserCouponPageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.promotion.CouponVO;
import com.my.petverse.promotion.service.DiscountService;
import com.my.petverse.promotion.service.UserCouponService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 用户优惠券接口控制器（领取/兑换/我的券/最优方案查询）。
 * 核销与退券等内部能力见 InternalUserCouponController，不对外暴露
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/promotion/user-coupons")
public class UserCouponController {

    private final UserCouponService userCouponService;

    private final DiscountService discountService;

    /** 领取优惠券 */
    @PostMapping("/{couponId}/receive")
    public Result<Void> receiveCoupon(@PathVariable("couponId") Long couponId) {
        userCouponService.receiveCoupon(couponId);
        return Result.success();
    }

    /** 兑换码兑换优惠券 */
    @PostMapping("/{code}/exchange")
    public Result<Void> exchangeCoupon(@PathVariable("code") String code) {
        userCouponService.exchangeCoupon(code);
        return Result.success();
    }

    /** 分页查询我的优惠券 */
    @GetMapping("/page")
    public Result<PageResult<CouponVO>> queryMyCouponPage(@Valid UserCouponPageQueryDTO query) {
        return Result.success(userCouponService.queryMyCouponPage(query));
    }

    /** 查询我的优惠券在当前订单商品下的可用方案（智能最优排序） */
    @PostMapping("/available")
    public Result<List<CouponDiscountDTO>> findDiscountSolution(@RequestBody List<OrderProductDTO> orderProducts) {
        return Result.success(discountService.findDiscountSolution(orderProducts));
    }

    /** 查询已用优惠券的规则描述 */
    @GetMapping("/rules")
    public Result<List<String>> queryDiscountRules(@RequestParam("couponIds") List<Long> userCouponIds) {
        return Result.success(userCouponService.queryDiscountRules(userCouponIds));
    }
}
