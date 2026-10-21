package com.my.petverse.common.vo.promotion;

import com.my.petverse.common.enums.DiscountType;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 用户端优惠券信息
 */
@Data
public class CouponVO implements Serializable {

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

    /** 折扣值：满减为抵扣金额（分），打折为折扣值（80 表示 8 折） */
    private Integer discountValue;

    /** 最大优惠金额（分） */
    private Integer maxDiscountAmount;

    /** 有效天数 */
    private Integer termDays;

    /** 使用有效期结束时间 */
    private LocalDateTime termEndTime;

    /** 是否可以领取（库存未领完且未超每人限领） */
    private Boolean available;

    /** 是否可以使用（当前用户已领取且未使用） */
    private Boolean received;
}
