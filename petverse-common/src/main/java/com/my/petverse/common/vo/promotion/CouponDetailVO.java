package com.my.petverse.common.vo.promotion;

import com.my.petverse.common.enums.DiscountType;
import com.my.petverse.common.enums.ObtainType;
import lombok.Data;
import org.springframework.format.annotation.DateTimeFormat;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 优惠券详细数据（管理端）
 */
@Data
public class CouponDetailVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 优惠券 id */
    private Long id;

    /** 优惠券名称 */
    private String name;

    /** 优惠券使用范围 */
    private List<CouponScopeVO> scopes;

    /** 折扣类型：1-每满减 2-折扣 3-无门槛 4-满减 */
    private DiscountType discountType;

    /** 折扣门槛（分），0 代表无门槛 */
    private Integer thresholdAmount;

    /** 折扣值 */
    private Integer discountValue;

    /** 最大优惠金额（分） */
    private Integer maxDiscountAmount;

    /** 发放开始时间 */
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime issueBeginTime;

    /** 发放结束时间 */
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime issueEndTime;

    /** 有效天数 */
    private Integer termDays;

    /** 使用有效期开始时间 */
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime termBeginTime;

    /** 使用有效期结束时间 */
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime termEndTime;

    /** 优惠券总量，0 代表无上限 */
    private Integer totalNum;

    /** 每人领取上限 */
    private Integer userLimit;

    /** 获取方式：1-手动领取 2-发放兑换码 */
    private ObtainType obtainWay;
}
