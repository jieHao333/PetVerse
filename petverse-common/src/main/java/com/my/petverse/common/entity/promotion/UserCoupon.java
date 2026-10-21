package com.my.petverse.common.entity.promotion;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import com.my.petverse.common.enums.UserCouponStatus;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.time.LocalDateTime;

/**
 * 用户领取优惠券的记录实体，对应数据库表 user_coupon（真正使用的优惠券信息）
 */
@Data
@EqualsAndHashCode(callSuper = true)
@Accessors(chain = true)
@TableName("user_coupon")
public class UserCoupon extends BaseEntity {

    /** 优惠券拥有者 */
    private Long userId;

    /** 优惠券模板 id */
    private Long couponId;

    /** 优惠券有效期开始时间 */
    private LocalDateTime termBeginTime;

    /** 优惠券有效期结束时间 */
    private LocalDateTime termEndTime;

    /** 优惠券使用时间（核销时间） */
    private LocalDateTime usedTime;

    /** 优惠券状态：1-未使用 2-已使用 3-已失效 */
    private UserCouponStatus status;
}
