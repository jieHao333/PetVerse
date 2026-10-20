package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

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

    /** 店铺状态：1-营业 0-打烊 2-封禁 */
    private Integer status;

    /** 封禁原因（管理员封禁时填写） */
    private String banReason;

    /** 封禁时间 */
    private LocalDateTime banTime;

    /** 商家身份撤销状态：0-正常 1-冷静期 */
    private Integer withdrawStatus;

    /** 撤销冷静期截止时间，到期后清除店铺数据 */
    private LocalDateTime withdrawDeadline;

    /** 店铺当前是否可对外售卖（营业中且不在撤销冷静期） */
    public boolean isSelling() {
        return status != null && status == 1 && !isWithdrawing();
    }

    /** 是否处于商家身份撤销冷静期 */
    public boolean isWithdrawing() {
        return withdrawStatus != null && withdrawStatus == 1;
    }

    /** 店铺是否已被管理员封禁 */
    public boolean isBanned() {
        return status != null && status == 2;
    }
}
