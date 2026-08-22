package com.my.petverse.common.dto.pet;

import com.my.petverse.common.enums.PetClaimMethod;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 新用户领取宠物请求参数
 */
@Data
public class PetClaimDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    @NotNull(message = "用户ID不能为空")
    private Long userId;

    /** 领取方式：RANDOM-随机抽取 CHOOSE-自选 */
    @NotNull(message = "领取方式不能为空")
    private PetClaimMethod method;

    /** 自选时对应的图鉴ID（method 为 CHOOSE 时必填） */
    private Long catalogId;

    /** 用户为宠物取的名字 */
    @NotBlank(message = "请给宠物取个名字")
    @Size(max = 50, message = "宠物名称不能超过50个字符")
    private String name;
}
