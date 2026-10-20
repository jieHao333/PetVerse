package com.my.petverse.shop.search;

import com.my.petverse.common.dto.shop.ProductPageQueryDTO;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.result.PageResult;

import java.util.Set;

/**
 * 商品全文检索服务：Elasticsearch 只作为检索索引，展示数据仍以 MySQL 为准。
 * 所有方法在 ES 不可用时均不抛异常，由调用方降级为数据库查询。
 * 索引中的商品状态为“对外可售状态”：商品上架且店铺营业时才是 1，店铺打烊/封禁/撤销中一律按 0 处理。
 */
public interface ProductSearchService {

    /** ES 检索是否可用（配置开启且当前未处于降级窗口内） */
    boolean isAvailable();

    /** 新增或更新单个商品的索引（shopSelling 表示所属店铺当前是否可对外售卖） */
    void indexProduct(Product product, String shopName, boolean shopSelling);

    /** 删除单个商品的索引 */
    void removeProduct(Long productId);

    /** 店铺改名或营业状态变更后重刷该商家全部商品的索引，保证搜索可见性与店铺名正确 */
    void refreshMerchantProducts(Long merchantId);

    /** 索引不存在时创建索引并全量灌入库中已有数据，返回灌入条数 */
    long initIndexIfAbsent();

    /**
     * 按关键词检索商品，返回按相关度排序的商品ID分页结果。
     *
     * @param status     商品状态过滤，为空不限制（买家商城传 1 只搜可售商品）
     * @param merchantId 商家过滤，为空不限制（商家管理列表传自己的商家ID）
     * @return ES 不可用或检索失败时返回 null，调用方需降级为数据库查询
     */
    PageResult<Long> searchIds(ProductPageQueryDTO query, String keyword, Integer status, Long merchantId);

    /**
     * 拉取索引中全部商品ID，供索引对账任务比对 MySQL 与 ES 的差集使用。
     * 分页扫描，受 ES from+size 上限约束，单次最多覆盖一万条，超出部分记日志提示。
     *
     * @return ES 不可用或扫描失败时返回 null
     */
    Set<Long> listIndexedIds();
}
