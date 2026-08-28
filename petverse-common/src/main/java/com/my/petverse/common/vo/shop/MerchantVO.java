package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 商家店铺返回实体
 */
@Data
public class MerchantVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商家ID */
    private Long id;

    /** 店主用户ID */
    private Long userId;

    /** 店铺名称 */
    private String shopName;

    /** 店铺LOGO */
    private String shopLogo;

    /** 店铺简介 */
    private String description;

    /** 联系电话 */
    private String contactPhone;

    /** 店铺状态：1-营业 0-封禁 */
    private Integer status;

    /** 开店时间 */
    private LocalDateTime createTime;
}
