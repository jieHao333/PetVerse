package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 店铺公开信息返回实体（买家店家页面展示，不包含封禁原因等管理端字段）
 */
@Data
public class StoreVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商家ID */
    private Long id;

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

    /** 撤销状态：0-正常 1-冷静期 */
    private Integer withdrawStatus;

    /** 当前是否可对外售卖 */
    private Boolean selling;

    /** 开店时间 */
    private LocalDateTime createTime;
}
