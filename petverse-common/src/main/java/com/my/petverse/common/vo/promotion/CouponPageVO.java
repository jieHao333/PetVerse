package com.my.petverse.common.vo.promotion;

import com.my.petverse.common.enums.CouponStatus;
import com.my.petverse.common.enums.DiscountType;
import com.my.petverse.common.enums.ObtainType;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 优惠券管理端分页数据
 */
@Data
public class CouponPageVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 优惠券 id */
    private Long id;

    /** 优惠券名称 */
    private String name;

    /** 是否限定使用范围 */
    private Boolean specific;

    /** 折扣类型：1-每满减 2-折扣 3-无门槛 4-满减 */
    private DiscountType discountType;

    /** 折扣门槛（分），0 代表无门槛 */
    private Integer thresholdAmount;

    /** 折扣值 */
    private Integer discountValue;

    /** 最大优惠金额（分） */
    private Integer maxDiscountAmount;

    /** 获取方式：1-手动领取 2-发放兑换码 */
    private ObtainType obtainWay;

    /** 已使用数量 */
    private Integer usedNum;

    /** 已发放数量 */
    private Integer issueNum;

    /** 优惠券总量 */
    private Integer totalNum;

    /** 优惠券创建时间 */
    private LocalDateTime createTime;

    /** 发放开始时间 */
    private LocalDateTime issueBeginTime;

    /** 发放结束时间 */
    private LocalDateTime issueEndTime;

    /** 有效天数 */
    private Integer termDays;

    /** 使用有效期开始时间 */
    private LocalDateTime termBeginTime;

    /** 使用有效期结束时间 */
    private LocalDateTime termEndTime;

    /** 优惠券状态 */
    private CouponStatus status;
}
