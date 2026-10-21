package com.my.petverse.promotion.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.promotion.Coupon;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

import java.util.List;

/**
 * 优惠券 Mapper 接口
 */
public interface CouponMapper extends BaseMapper<Coupon> {

    /** 发放数量 +1（条件更新防超发：issue_num < total_num 才更新） */
    @Update("UPDATE coupon SET issue_num = issue_num + 1 WHERE id = #{couponId} AND issue_num < total_num")
    int incrIssueNum(@Param("couponId") Long couponId);

    /** 已使用数量自增（amount 可为负数表示退还） */
    int incrUsedNum(List<Long> couponIds, int amount);
}
