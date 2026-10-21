package com.my.petverse.common.entity.promotion;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import com.my.petverse.common.enums.CouponStatus;
import com.my.petverse.common.enums.DiscountType;
import com.my.petverse.common.enums.ObtainType;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.time.LocalDateTime;

/**
 * 优惠券实体，对应数据库表 coupon（优惠券的规则信息）
 * 金额字段统一以“分”为单位存储（Integer），展示时按 2 位小数格式化为元
 */
@Data
@EqualsAndHashCode(callSuper = true)
@Accessors(chain = true)
@TableName("coupon")
public class Coupon extends BaseEntity {

    /** 优惠券名称 */
    @TableField("`name`")
    private String name;

    /** 优惠券类型：1-普通券（保留字段） */
    private Integer type;

    /** 折扣类型：1-每满减 2-折扣 3-无门槛 4-满减 */
    private DiscountType discountType;

    /** 是否限定作用范围：false-不限定 true-限定，默认 false */
    @TableField("`specific`")
    private Boolean specific;

    /** 折扣值：满减存抵扣金额（分），折扣存折扣率（80 表示 8 折） */
    private Integer discountValue;

    /** 使用门槛（分）：0 表示无门槛，其他值为最低消费金额 */
    private Integer thresholdAmount;

    /** 最高优惠金额（分）：0 表示无限制 */
    private Integer maxDiscountAmount;

    /** 获取方式：1-手动领取 2-兑换码 */
    private ObtainType obtainWay;

    /** 开始发放时间 */
    private LocalDateTime issueBeginTime;

    /** 结束发放时间 */
    private LocalDateTime issueEndTime;

    /** 优惠券有效期天数：0 表示有效期是指定有效期的（配合 termBeginTime/termEndTime） */
    private Integer termDays;

    /** 优惠券有效期开始时间 */
    private LocalDateTime termBeginTime;

    /** 优惠券有效期结束时间 */
    private LocalDateTime termEndTime;

    /** 优惠券状态：1-待发放 2-未开始 3-发放中 4-已结束 5-暂停 */
    private CouponStatus status;

    /** 总数量，不超过 5000 */
    private Integer totalNum;

    /** 已发行数量，用于判断是否超发 */
    private Integer issueNum;

    /** 已使用数量 */
    private Integer usedNum;

    /** 每人限领数量，默认 1 */
    private Integer userLimit;

    /** 拓展参数字段（保留字段） */
    private String extParam;

    /**
     * 领券记录 id，仅在优惠方案计算联表查询时由 user_coupon.id 别名填充，
     * 用于在方案结果中回传用户优惠券 id（非表字段）
     */
    @TableField(exist = false)
    private Long userCouponId;
}
