package com.my.petverse.common.vo.shop;

import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 订单返回实体（含明细列表）
 */
@Data
public class OrderVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 订单ID */
    private Long id;

    /** 订单号 */
    private String orderNo;

    /** 买家用户ID */
    private Long userId;

    /** 商家ID */
    private Long merchantId;

    /** 店铺名称 */
    private String shopName;

    /** 订单总金额(元) */
    private BigDecimal totalAmount;

    /** 订单状态：0-待支付 1-待取货 2-已完成 3-已取消 */
    private Integer status;

    /** 订单状态名称 */
    private String statusName;

    /** 取货方式：1-到店自取（预留 2-外卖配送） */
    private Integer pickupType;

    /** 取货方式名称 */
    private String pickupTypeName;

    /** 取货码（支付后生成，仅买家与商家可见） */
    private String pickupCode;

    /** 买家备注 */
    private String remark;

    /** 支付时间 */
    private LocalDateTime payTime;

    /** 完成时间（到店核销时间） */
    private LocalDateTime finishTime;

    /** 下单时间 */
    private LocalDateTime createTime;

    /** 支付截止时间（仅待支付订单返回，超时未支付将自动取消并回补库存，前端据此倒计时） */
    private LocalDateTime payDeadline;

    /** 订单明细列表 */
    private List<OrderItemVO> items;
}
