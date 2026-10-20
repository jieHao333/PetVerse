package com.my.petverse.common.dto.shop;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 管理端商家店铺分页查询参数
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class MerchantAdminPageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 店铺状态：1-营业 0-打烊 2-封禁，为空查全部 */
    private Integer status;

    /** 是否处于撤销冷静期：true-仅撤销中 false-排除撤销中，为空不限制 */
    private Boolean withdrawing;

    /** 店铺名称（模糊匹配） */
    private String shopName;
}
