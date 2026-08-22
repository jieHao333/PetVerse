package com.my.petverse.common.dto.pet;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 宠物经验发放请求参数
 */
@Data
public class PetExpGrantDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    @NotNull(message = "用户ID不能为空")
    private Long userId;

    /** 经验来源，取值见 PetExpSource 枚举 */
    @NotNull(message = "经验来源不能为空")
    private String source;
}
