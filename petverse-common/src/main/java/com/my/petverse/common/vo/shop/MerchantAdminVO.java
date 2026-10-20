package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 管理端商家店铺返回实体（含店主昵称、封禁与撤销冷静期信息）
 */
@Data
public class MerchantAdminVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商家ID */
    private Long id;

    /** 店主用户ID */
    private Long userId;

    /** 店主昵称 */
    private String ownerNickname;

    /** 店铺名称 */
    private String shopName;

    /** 店铺LOGO */
    private String shopLogo;

    /** 店铺简介 */
    private String description;

    /** 联系电话 */
    private String contactPhone;

    /** 店铺状态：1-营业 0-打烊 2-封禁 */
    private Integer status;

    /** 封禁原因 */
    private String banReason;

    /** 封禁时间 */
    private LocalDateTime banTime;

    /** 撤销状态：0-正常 1-冷静期 */
    private Integer withdrawStatus;

    /** 撤销冷静期截止时间 */
    private LocalDateTime withdrawDeadline;

    /** 当前是否可对外售卖 */
    private Boolean selling;

    /** 开店时间 */
    private LocalDateTime createTime;
}
