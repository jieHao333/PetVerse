package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 商家入驻申请单返回实体
 */
@Data
public class MerchantApplyVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 申请单ID */
    private Long id;

    /** 申请人用户ID */
    private Long userId;

    /** 申请人昵称（管理端列表聚合展示） */
    private String applicantNickname;

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

    /** 审批时间 */
    private LocalDateTime auditTime;

    /** 申请时间 */
    private LocalDateTime createTime;
}
