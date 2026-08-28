package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 商家入驻申请重新提交请求参数（驳回后修改重提）
 */
@Data
public class MerchantApplyUpdateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 申请单ID */
    @NotNull(message = "申请单ID不能为空")
    private Long id;

    /** 店铺名称 */
    @NotBlank(message = "店铺名称不能为空")
    @Size(max = 50, message = "店铺名称不能超过50个字符")
    private String shopName;

    /** 联系人姓名 */
    @NotBlank(message = "联系人姓名不能为空")
    @Size(max = 30, message = "联系人姓名不能超过30个字符")
    private String contactName;

    /** 联系电话 */
    @NotBlank(message = "联系电话不能为空")
    @Pattern(regexp = "^1[3-9]\\d{9}$", message = "联系电话格式不正确")
    private String contactPhone;

    /** 营业执照号 */
    @NotBlank(message = "营业执照号不能为空")
    @Size(max = 50, message = "营业执照号不能超过50个字符")
    private String licenseNo;

    /** 营业执照图片(OSS地址) */
    @NotBlank(message = "请上传营业执照图片")
    @Size(max = 255, message = "营业执照图片地址过长")
    private String licenseUrl;

    /** 店铺简介 */
    @Size(max = 500, message = "店铺简介不能超过500个字符")
    private String description;
}
