package com.my.petverse.common.dto.pet;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 设置出场宠物请求参数
 */
@Data
public class PetSetActiveDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    @NotNull(message = "用户ID不能为空")
    private Long userId;

    /** 要设置为出场的宠物ID */
    @NotNull(message = "宠物ID不能为空")
    private Long petId;
}
