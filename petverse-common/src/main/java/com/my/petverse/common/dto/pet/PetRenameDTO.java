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

    /** 用户ID（一个用户仅有一只宠物，按用户定位宠物） */
    @NotNull(message = "用户ID不能为空")
    private Long userId;

    /** 新的宠物名称 */
    @NotBlank(message = "宠物名称不能为空")
    @Size(max = 50, message = "宠物名称不能超过50个字符")
    private String name;
}
