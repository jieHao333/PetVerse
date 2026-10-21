package com.my.petverse.common.dto.promotion;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;

/** 优惠券管理端分页查询参数 */
@EqualsAndHashCode(callSuper = true)
@Data
public class CouponPageQueryDTO extends BasePageQuery {

    /** 折扣类型：1-每满减 2-折扣 3-无门槛 4-满减 */
    private Integer type;

    /** 优惠券状态：1-待发放 2-未开始 3-发放中 4-已结束 5-暂停 */
    private Integer status;

    /** 优惠券名称（模糊匹配） */
    private String name;
}
