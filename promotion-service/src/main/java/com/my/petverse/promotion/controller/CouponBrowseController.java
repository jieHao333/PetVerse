package com.my.petverse.promotion.controller;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.promotion.CouponVO;
import com.my.petverse.promotion.service.CouponService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 优惠券领券页接口（用户端，游客可浏览；登录后返回当前用户的领取/可用状态）
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/promotion/coupons")
public class CouponBrowseController {

    private final CouponService couponService;

    /** 查询发放中的优惠券列表 */
    @GetMapping("/list")
    public Result<List<CouponVO>> queryIssuingCoupons() {
        return Result.success(couponService.queryIssuingCoupons());
    }
}
