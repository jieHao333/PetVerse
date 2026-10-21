package com.my.petverse.promotion.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.common.entity.promotion.UserCoupon;
import com.my.petverse.common.enums.UserCouponStatus;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 用户优惠券 Mapper 接口
 */
public interface UserCouponMapper extends BaseMapper<UserCoupon> {

    /** 查询我的所有可用优惠券（联表，userCouponId 回填到 Coupon 的非表字段） */
    List<Coupon> queryMyCoupons(@Param("userId") Long userId);

    /** 按用户券 id 集合与状态查询优惠券（联表） */
    List<Coupon> queryCouponByUserCouponIds(
            @Param("userCouponIds") List<Long> userCouponIds, @Param("status") UserCouponStatus status);
}
