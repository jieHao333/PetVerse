package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * 商家入驻申请单实体，对应数据库表 merchant_apply
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("merchant_apply")
public class MerchantApply extends BaseEntity {

    /** 申请人用户ID */
    private Long userId;

    /** 店铺名称 */
    private String shopName;

    /** 联系人姓名 */
    private String contactName;

    /** 联系电话 */
    private String contactPhone;

    /** 营业执照号 */
    private String licenseNo;

    /** 营业执照图片(OSS地址) */
    private String licenseUrl;

    /** 店铺简介 */
    private String description;

    /** 审批状态：0-待审核 1-已通过 2-已驳回 */
    private Integer status;

    /** 驳回原因 */
    private String rejectReason;

    /** 审批管理员ID */
    private Long auditUserId;

    /** 审批时间 */
    private LocalDateTime auditTime;
}
