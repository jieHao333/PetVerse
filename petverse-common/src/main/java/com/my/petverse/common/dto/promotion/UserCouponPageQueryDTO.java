package com.my.petverse.common.dto.promotion;

import com.my.petverse.common.dto.base.BasePageQuery;
import jakarta.validation.constraints.NotNull;
import lombok.Data;
import lombok.EqualsAndHashCode;

/** 用户优惠券分页查询参数 */
@EqualsAndHashCode(callSuper = true)
@Data
public class UserCouponPageQueryDTO extends BasePageQuery {

    /** 优惠券状态：1-未使用 2-已使用 3-已过期 */
    @NotNull(message = "优惠券状态不能为空")
    private Integer status;
}
