package com.my.petverse.common.entity.shop;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 订单实体，对应数据库表 shop_order（order 为 MySQL 保留字，表名加 shop_ 前缀）
 * 目前仅支持到店自取，pickupType 预留外卖配送扩展
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("shop_order")
public class ShopOrder extends BaseEntity {

    /** 订单号 */
    private String orderNo;

    /** 买家用户ID */
    private Long userId;

    /** 商家ID（一单一店） */
    private Long merchantId;

    /** 订单总金额(元) */
    private BigDecimal totalAmount;

    /** 订单状态：0-待支付 1-待取货 2-已完成 3-已取消 */
    private Integer status;

    /** 取货方式：1-到店自取（预留 2-外卖配送） */
    private Integer pickupType;

    /** 取货码（支付后生成，到店核销凭证） */
    private String pickupCode;

    /** 买家备注 */
    private String remark;

    /** 支付时间 */
    private LocalDateTime payTime;

    /** 完成时间（到店核销时间） */
    private LocalDateTime finishTime;

    /** 取消时间 */
    private LocalDateTime cancelTime;
}
