package com.my.petverse.shop.search;

import com.my.petverse.common.dto.shop.ProductPageQueryDTO;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.result.PageResult;

/**
 * 商品全文检索服务：Elasticsearch 只作为检索索引，展示数据仍以 MySQL 为准。
 * 所有方法在 ES 不可用时均不抛异常，由调用方降级为数据库查询。
 */
public interface ProductSearchService {

    /** ES 检索是否可用（配置开启且当前未处于降级窗口内） */
    boolean isAvailable();

    /** 新增或更新单个商品的索引 */
    void indexProduct(Product product, String shopName);

    /** 删除单个商品的索引 */
    void removeProduct(Long productId);

    /** 店铺改名后重刷该商家全部商品的索引，保证按店铺名搜索的结果正确 */
    void refreshShopName(Long merchantId, String shopName);

    /** 索引不存在时创建索引并全量灌入库中已有数据，返回灌入条数 */
    long initIndexIfAbsent();

    /**
     * 按关键词检索商品，返回按相关度排序的商品ID分页结果。
     *
     * @param status     商品状态过滤，为空不限制（买家商城传 1 只搜上架商品）
     * @param merchantId 商家过滤，为空不限制（商家管理列表传自己的商家ID）
     * @return ES 不可用或检索失败时返回 null，调用方需降级为数据库查询
     */
    PageResult<Long> searchIds(ProductPageQueryDTO query, String keyword, Integer status, Long merchantId);
}
