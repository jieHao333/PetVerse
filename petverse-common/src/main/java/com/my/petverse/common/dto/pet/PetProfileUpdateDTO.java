package com.my.petverse.common.dto.pet;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDate;

/**
 * 完善/修改真实宠物档案请求参数
 * 首页「完善/修改宠物信息」入口提交：种类、品种、性别、生日、是否绝育、收养时间
 */
@Data
public class PetProfileUpdateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID，由服务端从登录令牌解析写入，客户端无需传入 */
    private Long userId;

    /** 宠物ID */
    @NotNull(message = "宠物ID不能为空")
    private Long petId;

    /** 宠物种类（物种），如：猫、狗 */
    @Size(max = 50, message = "宠物种类不能超过50个字符")
    private String species;

    /** 宠物品种，如：布偶猫、金毛寻回犬 */
    @Size(max = 50, message = "宠物品种不能超过50个字符")
    private String breed;

    /** 性别 1-弟弟 2-妹妹 */
    private Integer gender;

    /** 生日 */
    private LocalDate birthday;

    /** 是否绝育 0-否 1-是 */
    private Integer sterilized;

    /** 收养时间 */
    private LocalDate adoptionDate;
}
