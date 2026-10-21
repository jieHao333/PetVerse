package com.my.petverse.promotion.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.promotion.CouponFormDTO;
import com.my.petverse.common.dto.promotion.CouponIssueFormDTO;
import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.common.dto.promotion.CouponPageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.promotion.CouponDetailVO;
import com.my.petverse.common.vo.promotion.CouponPageVO;
import com.my.petverse.common.vo.promotion.CouponVO;

import java.util.List;

/**
 * 优惠券（规则信息）服务类
 */
public interface CouponService extends IService<Coupon> {

    /** 新增优惠券 */
    void saveCoupon(CouponFormDTO dto);

    /** 管理端分页查询优惠券 */
    PageResult<CouponPageVO> queryCouponByPage(CouponPageQueryDTO query);

    /** 发放优惠券（立即或定时） */
    void beginIssue(CouponIssueFormDTO dto);

    /** 查询发放中的优惠券列表（用户端领券页） */
    List<CouponVO> queryIssuingCoupons();

    /** 暂停发放 */
    void pauseIssue(Long id);

    /** 删除待发放的优惠券 */
    void deleteById(Long id);

    /** 查询优惠券详情（含限定范围名称） */
    CouponDetailVO queryCouponById(Long id);

    /** 批量开始发放（定时任务到点触发的券） */
    void beginIssueBatch(List<Coupon> coupons);
}
