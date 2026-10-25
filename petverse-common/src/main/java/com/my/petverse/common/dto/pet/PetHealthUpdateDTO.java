package com.my.petverse.common.dto.pet;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 宠物健康信息单项更新请求参数（猫/狗身份卡健康模块）
 * 每次仅更新一个类别，category 为类别键，value 为内容（空串表示清空该项）
 */
@Data
public class PetHealthUpdateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID（用于归属校验），由服务端从登录令牌解析写入，客户端无需传入 */
    private Long userId;

    /** 宠物ID */
    @NotNull(message = "宠物ID不能为空")
    private Long petId;

    /** 健康信息类别键：weight/bcs/deworming/specialPeriod/vaccine/rearingMethod/medicalHistory */
    @NotBlank(message = "健康信息类别不能为空")
    private String category;

    /** 健康信息内容，空串表示清空该项 */
    @Size(max = 500, message = "健康信息内容不能超过500个字符")
    private String value;

    /** 内容单位，如体重 kg/g、BCS 分；无单位类别可为空 */
    @Size(max = 20, message = "健康信息单位不能超过20个字符")
    private String unit;

    /** 记录时间，ISO 格式 yyyy-MM-ddTHH:mm:ss；旧客户端不传时由服务端补当前时间 */
    private LocalDateTime recordTime;
}
