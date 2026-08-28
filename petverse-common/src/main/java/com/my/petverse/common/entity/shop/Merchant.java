package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 商家店铺实体，对应数据库表 merchant（入驻审批通过后生成）
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("merchant")
public class Merchant extends BaseEntity {

    /** 店主用户ID */
    private Long userId;

    /** 来源申请单ID */
    private Long applyId;

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
}
