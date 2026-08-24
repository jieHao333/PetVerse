package com.my.petverse.common.dto.pet;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 宠物改名请求参数
 */
@Data
public class PetRenameDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID（用于归属校验） */
    @NotNull(message = "用户ID不能为空")
    private Long userId;

    /** 宠物ID（多宠后按宠物ID定位） */
    @NotNull(message = "宠物ID不能为空")
    private Long petId;

    /** 新的宠物名称 */
    @NotBlank(message = "宠物名称不能为空")
    @Size(max = 50, message = "宠物名称不能超过50个字符")
    private String name;
}
