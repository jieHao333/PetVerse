package com.my.petverse.common.dto.shop;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 商品分页查询参数（买家商城浏览 / 商家管理列表共用）
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class ProductPageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 搜索关键词，走 Elasticsearch 全文检索商品名、详情与店铺名；ES 不可用时降级为数据库模糊匹配 */
    private String keyword;

    /** 商品名称（模糊匹配），keyword 为空时生效，保留以兼容旧调用 */
    private String name;

    /** 商品类型：1-宠物用品 2-宠物食品 3-活体宠物，为空查全部 */
    private Integer category;

    /** 所属商家ID（查看指定店铺的商品） */
    private Long merchantId;
}
