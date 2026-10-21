package com.my.petverse.common.dto.promotion;

import com.my.petverse.common.dto.base.BasePageQuery;
import jakarta.validation.constraints.NotNull;
import lombok.Data;
import lombok.EqualsAndHashCode;

/** 兑换码分页查询参数 */
@EqualsAndHashCode(callSuper = true)
@Data
public class CodePageQueryDTO extends BasePageQuery {

    /** 兑换码对应的优惠券 id */
    @NotNull(message = "优惠券id不能为空")
    private Long couponId;

    /** 兑换码状态：1-待兑换 2-已兑换 */
    @NotNull(message = "兑换码状态不能为空")
    private Integer status;
}
