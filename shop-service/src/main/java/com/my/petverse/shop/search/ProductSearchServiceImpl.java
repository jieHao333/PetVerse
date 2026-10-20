package com.my.petverse.shop.search;

import co.elastic.clients.elasticsearch._types.SortOptions;
import co.elastic.clients.elasticsearch._types.SortOrder;
import co.elastic.clients.elasticsearch._types.query_dsl.BoolQuery;
import co.elastic.clients.elasticsearch._types.query_dsl.TextQueryType;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.my.petverse.common.document.shop.ProductDoc;
import com.my.petverse.common.dto.shop.ProductPageQueryDTO;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.elasticsearch.client.elc.NativeQuery;
import org.springframework.data.elasticsearch.core.ElasticsearchOperations;
import org.springframework.data.elasticsearch.core.IndexOperations;
import org.springframework.data.elasticsearch.core.SearchHit;
import org.springframework.data.elasticsearch.core.SearchHits;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 商品全文检索服务实现：写入时双写索引，检索时只取文档ID再回表。
 * ES 异常一律降级处理并进入短暂的降级窗口，避免每个请求都等待连接超时。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ProductSearchServiceImpl implements ProductSearchService {

    private final ElasticsearchOperations elasticsearchOperations;

    private final ProductMapper productMapper;

    private final MerchantMapper merchantMapper;

    /** 搜索总开关，关闭后全部走数据库模糊查询 */
    @Value("${petverse.search.enabled:true}")
    private boolean searchEnabled;

    /** ES 异常后的降级窗口时长 */
    private static final long DEGRADE_WINDOW_MILLIS = 60_000L;

    /** 全量重建索引时的每批条数 */
    private static final int REINDEX_BATCH_SIZE = 500;

    /** 索引ID全量扫描单页条数 */
    private static final int ID_SCAN_PAGE_SIZE = 1000;

    /** 索引ID全量扫描最大页数（ES 默认 from+size 单次不超过一万） */
    private static final int ID_SCAN_MAX_PAGES = 10;

    /** 降级截止时间戳，该时刻之前不再访问 ES */
    private volatile long degradedUntil = 0L;

    @Override
    public boolean isAvailable() {
        return searchEnabled && System.currentTimeMillis() >= degradedUntil;
    }

    @Override
    public void indexProduct(Product product, String shopName, boolean shopSelling) {
        if (!searchEnabled || product == null) {
            return;
        }
        try {
            elasticsearchOperations.save(toDoc(product, shopName, shopSelling));
        } catch (Exception e) {
            markDegraded("写入商品索引失败，productId=" + product.getId(), e);
        }
    }

    @Override
    public void removeProduct(Long productId) {
        if (!searchEnabled || productId == null) {
            return;
        }
        try {
            elasticsearchOperations.delete(String.valueOf(productId), ProductDoc.class);
        } catch (Exception e) {
            markDegraded("删除商品索引失败，productId=" + productId, e);
        }
    }

    @Override
    public void refreshMerchantProducts(Long merchantId) {
        if (!searchEnabled || merchantId == null) {
            return;
        }
        try {
            Merchant merchant = merchantMapper.selectById(merchantId);
            if (merchant == null) {
                return;
            }
            List<Product> products = productMapper.selectList(new LambdaQueryWrapper<Product>()
                    .eq(Product::getMerchantId, merchantId));
            if (products.isEmpty()) {
                return;
            }
            boolean shopSelling = merchant.isSelling();
            elasticsearchOperations.save(products.stream()
                    .map(product -> toDoc(product, merchant.getShopName(), shopSelling))
                    .toList());
        } catch (Exception e) {
            markDegraded("刷新店铺商品索引失败，merchantId=" + merchantId, e);
        }
    }

    @Override
    public long initIndexIfAbsent() {
        if (!searchEnabled) {
            log.info("商品搜索已关闭，跳过 Elasticsearch 索引初始化");
            return 0L;
        }
        try {
            IndexOperations indexOperations = elasticsearchOperations.indexOps(ProductDoc.class);
            if (indexOperations.exists()) {
                return 0L;
            }
            indexOperations.createWithMapping();
            long total = reindexAll();
            log.info("商品索引创建完成，已灌入 {} 条数据", total);
            return total;
        } catch (Exception e) {
            markDegraded("商品索引初始化失败，搜索将降级为数据库模糊查询", e);
            return 0L;
        }
    }

    /** 按主键游标分批把库中商品全量写入索引，并批量补齐店铺名 */
    private long reindexAll() {
        long total = 0L;
        long lastId = 0L;
        while (true) {
            List<Product> batch = productMapper.selectList(new LambdaQueryWrapper<Product>()
                    .gt(Product::getId, lastId)
                    .orderByAsc(Product::getId)
                    .last("limit " + REINDEX_BATCH_SIZE));
            if (batch.isEmpty()) {
                break;
            }
            Map<Long, Merchant> shops = loadShops(batch);
            elasticsearchOperations.save(batch.stream()
                    .map(product -> {
                        Merchant merchant = shops.get(product.getMerchantId());
                        return toDoc(product, merchant == null ? null : merchant.getShopName(),
                                merchant != null && merchant.isSelling());
                    })
                    .toList());
            lastId = batch.get(batch.size() - 1).getId();
            total += batch.size();
        }
        return total;
    }

    /** 批量查询商品所属店铺 */
    private Map<Long, Merchant> loadShops(List<Product> products) {
        Set<Long> merchantIds = products.stream()
                .map(Product::getMerchantId)
                .collect(Collectors.toSet());
        if (merchantIds.isEmpty()) {
            return Map.of();
        }
        Map<Long, Merchant> shops = new HashMap<>();
        for (Merchant merchant : merchantMapper.selectBatchIds(merchantIds)) {
            shops.put(merchant.getId(), merchant);
        }
        return shops;
    }

    @Override
    public PageResult<Long> searchIds(ProductPageQueryDTO query, String keyword,
                                      Integer status, Long merchantId) {
        if (!isAvailable() || !StringUtils.hasText(keyword)) {
            return null;
        }
        try {
            NativeQuery nativeQuery = NativeQuery.builder()
                    .withQuery(q -> q.bool(buildQuery(query, keyword, status, merchantId)))
                    .withSort(List.of(
                            SortOptions.of(s -> s.score(sc -> sc.order(SortOrder.Desc))),
                            SortOptions.of(s -> s.field(f -> f.field("createTime").order(SortOrder.Desc)))))
                    .withPageable(PageRequest.of((int) (query.getPageNum() - 1), (int) query.getPageSize()))
                    .withTrackTotalHits(true)
                    .build();
            SearchHits<ProductDoc> hits = elasticsearchOperations.search(nativeQuery, ProductDoc.class);
            List<Long> ids = new ArrayList<>();
            for (SearchHit<ProductDoc> hit : hits.getSearchHits()) {
                ids.add(Long.valueOf(hit.getContent().getId()));
            }
            return PageResult.of(hits.getTotalHits(), query.getPageNum(), query.getPageSize(), ids);
        } catch (Exception e) {
            markDegraded("商品全文检索失败，降级为数据库模糊查询，keyword=" + keyword, e);
            return null;
        }
    }

    @Override
    public Set<Long> listIndexedIds() {
        if (!isAvailable()) {
            return null;
        }
        try {
            Set<Long> ids = new HashSet<>();
            // 仅按发布时间排序分页扫描文档ID，不取正文内容，降低单页开销
            for (int page = 0; page < ID_SCAN_MAX_PAGES; page++) {
                NativeQuery nativeQuery = NativeQuery.builder()
                        .withQuery(q -> q.matchAll(m -> m))
                        .withSort(SortOptions.of(s -> s.field(f -> f.field("createTime").order(SortOrder.Asc))))
                        .withPageable(PageRequest.of(page, ID_SCAN_PAGE_SIZE))
                        .build();
                List<SearchHit<ProductDoc>> hits = elasticsearchOperations
                        .search(nativeQuery, ProductDoc.class).getSearchHits();
                if (hits.isEmpty()) {
                    return ids;
                }
                for (SearchHit<ProductDoc> hit : hits) {
                    ids.add(Long.valueOf(hit.getContent().getId()));
                }
                if (hits.size() < ID_SCAN_PAGE_SIZE) {
                    return ids;
                }
            }
            log.warn("商品索引ID扫描达到上限 {} 条，更大数据量需改用滚动扫描", ID_SCAN_MAX_PAGES * ID_SCAN_PAGE_SIZE);
            return ids;
        } catch (Exception e) {
            markDegraded("扫描商品索引ID失败，降级为数据库模糊查询", e);
            return null;
        }
    }

    /**
     * 组装检索条件：关键词命中商品名、详情或店铺名即可（商品名短语完整命中时额外加权），
     * 状态、类型、商家均以 filter 形式参与，不影响相关度打分。
     */
    private BoolQuery buildQuery(ProductPageQueryDTO query, String keyword,
                                 Integer status, Long merchantId) {
        BoolQuery.Builder bool = new BoolQuery.Builder();
        // fuzziness 让用户只记得大概关键词（含错别字）时仍能召回，minimum_should_match 控制过度宽松
        bool.must(m -> m.bool(inner -> inner
                .should(s -> s.multiMatch(mm -> mm
                        .query(keyword)
                        .fields("name^3", "shopName^2", "description")
                        .type(TextQueryType.BestFields)
                        .fuzziness("AUTO")
                        .minimumShouldMatch("30%")))
                .should(s -> s.matchPhrase(mp -> mp.field("name").query(keyword).boost(4.0f)))
                .should(s -> s.matchPhrase(mp -> mp.field("description").query(keyword).boost(2.0f)))
                .minimumShouldMatch("1")));

        if (status != null) {
            bool.filter(f -> f.term(t -> t.field("status").value(status)));
        }
        if (merchantId != null) {
            bool.filter(f -> f.term(t -> t.field("merchantId").value(merchantId)));
        }
        if (query.getCategory() != null) {
            bool.filter(f -> f.term(t -> t.field("category").value(query.getCategory())));
        }
        return bool.build();
    }

    /** DO 转 ES 文档：status 记录对外可售状态，店铺未营业时即使商品上架也按 0 处理 */
    private ProductDoc toDoc(Product product, String shopName, boolean shopSelling) {
        ProductDoc doc = new ProductDoc();
        doc.setId(String.valueOf(product.getId()));
        doc.setName(product.getName());
        doc.setDescription(product.getDescription());
        doc.setShopName(shopName);
        doc.setCategory(product.getCategory());
        doc.setMerchantId(product.getMerchantId());
        boolean onSale = product.getStatus() != null && product.getStatus() == 1 && shopSelling;
        doc.setStatus(onSale ? 1 : 0);
        doc.setCreateTime(product.getCreateTime() == null
                ? System.currentTimeMillis() : toEpochMilli(product.getCreateTime()));
        return doc;
    }

    private long toEpochMilli(LocalDateTime time) {
        return time.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
    }

    /** 记录异常并进入降级窗口 */
    private void markDegraded(String message, Exception e) {
        degradedUntil = System.currentTimeMillis() + DEGRADE_WINDOW_MILLIS;
        log.warn("{}（后续{}秒内不再访问 Elasticsearch）", message, DEGRADE_WINDOW_MILLIS / 1000, e);
    }
}
