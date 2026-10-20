package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 管理员封禁商家参数
 */
@Data
public class MerchantBanDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 封禁原因（必填，便于商家了解违规情况） */
    @NotBlank(message = "封禁原因不能为空")
    @Size(max = 200, message = "封禁原因不能超过200个字符")
    private String reason;
}
