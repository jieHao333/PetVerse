package com.my.petverse.common.dto.shop;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 商家入驻申请分页查询参数（管理员审批列表）
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class MerchantApplyPageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 审批状态：0-待审核 1-已通过 2-已驳回，为空查全部 */
    private Integer status;

    /** 店铺名称（模糊匹配） */
    private String shopName;
}
