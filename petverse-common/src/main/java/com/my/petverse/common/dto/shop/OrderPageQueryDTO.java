package com.my.petverse.common.dto.shop;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 订单分页查询参数（买家我的订单 / 商家店铺订单共用）
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class OrderPageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 订单状态：0-待支付 1-待取货 2-已完成 3-已取消，为空查全部 */
    private Integer status;
}
