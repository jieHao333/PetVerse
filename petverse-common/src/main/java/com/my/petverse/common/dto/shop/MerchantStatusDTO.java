package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 商家切换店铺营业状态参数
 */
@Data
public class MerchantStatusDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 目标状态：1-营业 0-打烊 */
    @NotNull(message = "店铺状态不能为空")
    @Min(value = 0, message = "店铺状态不合法")
    @Max(value = 1, message = "店铺状态不合法")
    private Integer status;
}
