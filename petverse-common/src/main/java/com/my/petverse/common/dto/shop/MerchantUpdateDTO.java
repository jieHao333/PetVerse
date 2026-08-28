package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 商家店铺信息修改请求参数（商家本人维护）
 */
@Data
public class MerchantUpdateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 店铺名称 */
    @Size(max = 50, message = "店铺名称不能超过50个字符")
    private String shopName;

    /** 店铺LOGO */
    @Size(max = 255, message = "店铺LOGO地址过长")
    private String shopLogo;

    /** 店铺简介 */
    @Size(max = 500, message = "店铺简介不能超过500个字符")
    private String description;

    /** 联系电话 */
    @Pattern(regexp = "^1[3-9]\\d{9}$", message = "联系电话格式不正确")
    private String contactPhone;
}
