package com.my.petverse.promotion.controller;

import com.my.petverse.common.dto.promotion.CouponFormDTO;
import com.my.petverse.common.dto.promotion.CouponIssueFormDTO;
import com.my.petverse.common.dto.promotion.CouponPageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.promotion.CouponDetailVO;
import com.my.petverse.common.vo.promotion.CouponPageVO;
import com.my.petverse.promotion.service.CouponService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 优惠券管理接口控制器（管理端，网关校验 /api/promotion/admin 的 ADMIN 角色）
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/promotion/admin/coupons")
public class CouponController {

    private final CouponService couponService;

    /** 新增优惠券 */
    @PostMapping
    public Result<Void> saveCoupon(@RequestBody @Valid CouponFormDTO dto) {
        couponService.saveCoupon(dto);
        return Result.success();
    }

    /** 管理端分页查询优惠券 */
    @GetMapping("/page")
    public Result<PageResult<CouponPageVO>> queryCouponByPage(@Valid CouponPageQueryDTO query) {
        return Result.success(couponService.queryCouponByPage(query));
    }

    /** 根据id查询优惠券详情（含限定范围名称） */
    @GetMapping("/{id}")
    public Result<CouponDetailVO> queryCouponById(@PathVariable("id") Long id) {
        return Result.success(couponService.queryCouponById(id));
    }

    /** 发放优惠券（立即或定时） */
    @PutMapping("/{id}/issue")
    public Result<Void> beginIssue(@RequestBody @Valid CouponIssueFormDTO dto) {
        couponService.beginIssue(dto);
        return Result.success();
    }

    /** 暂停发放优惠券 */
    @PutMapping("/{id}/pause")
    public Result<Void> pauseIssue(@PathVariable("id") Long id) {
        couponService.pauseIssue(id);
        return Result.success();
    }

    /** 删除待发放的优惠券 */
    @DeleteMapping("/{id}")
    public Result<Void> deleteById(@PathVariable("id") Long id) {
        couponService.deleteById(id);
        return Result.success();
    }
}
