package com.my.petverse.common.dto.promotion;

import jakarta.validation.constraints.Future;
import jakarta.validation.constraints.NotNull;
import lombok.Data;
import org.springframework.format.annotation.DateTimeFormat;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 优惠券发放表单参数
 */
@Data
public class CouponIssueFormDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 优惠券 id */
    private Long id;

    /** 发放开始时间（为空或早于当前时间表示立刻发放） */
    @Future(message = "发放开始时间必须晚于当前时间")
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime issueBeginTime;

    /** 发放结束时间 */
    @Future(message = "发放结束时间必须晚于当前时间")
    @NotNull(message = "发放结束时间不能为空")
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime issueEndTime;

    /** 有效天数（termDays 与 termBeginTime/termEndTime 二选一） */
    private Integer termDays;

    /** 使用有效期开始时间 */
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime termBeginTime;

    /** 使用有效期结束时间 */
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime termEndTime;
}
