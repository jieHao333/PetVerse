package com.my.petverse.common.dto.pet;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDate;

/**
 * 登记真实宠物请求参数
 * 引导弹窗选择「已有宠物」时提交，名称（必填）、种类（必填）与收养时间（可选）
 */
@Data
public class PetRegisterDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID，由服务端从登录令牌解析写入，客户端无需传入 */
    private Long userId;

    /** 宠物名称 */
    @NotBlank(message = "请给宠物取个名字")
    @Size(max = 50, message = "宠物名称不能超过50个字符")
    private String name;

    /** 宠物种类（物种），如：猫、狗 */
    @NotBlank(message = "请选择宠物种类")
    @Size(max = 50, message = "宠物种类不能超过50个字符")
    private String species;

    /** 收养时间，可选 */
    private LocalDate adoptionDate;
}
