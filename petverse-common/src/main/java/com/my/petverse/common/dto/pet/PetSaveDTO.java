package com.my.petverse.common.dto.pet;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 新增宠物请求参数
 */
@Data
public class PetSaveDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 宠物名称 */
    @NotBlank(message = "宠物名称不能为空")
    @Size(max = 50, message = "宠物名称不能超过50个字符")
    private String name;

    /** 物种 */
    @NotBlank(message = "物种不能为空")
    @Size(max = 50, message = "物种不能超过50个字符")
    private String species;

    /** 品种 */
    @Size(max = 50, message = "品种不能超过50个字符")
    private String breed;

    /** 年龄 */
    @Min(value = 0, message = "年龄不能小于0")
    @Max(value = 100, message = "年龄不能大于100")
    private Integer age;

    /** 宠物描述 */
    @Size(max = 500, message = "描述不能超过500个字符")
    private String description;

    /** 形象图片地址 */
    private String imageUrl;

    /** 所属用户ID */
    private Long userId;
}
