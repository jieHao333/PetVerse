package com.my.petverse.shop.task;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import com.my.petverse.shop.search.ProductSearchService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 商品索引对账补偿任务：索引维护由 MQ 异步驱动，消息丢失或消费时写入被降级逻辑
 * 吞掉（不会触发 MQ 重试）会导致 ES 文档缺失或内容滞后，本任务以 MySQL 为准兜底：
 * 1）增量重推：按 update_time 水位重推最近变更的商品（每 5 分钟一轮，快速修复近期写入失败）；
 * 2）差集巡检：定期全量比对 MySQL 与 ES 的 ID 差集，补齐缺失文档、清理已删除商品的幽灵文档（每 30 分钟一轮）。
 * 重推与删除均幂等（重推前回表读最新数据与店铺名），多实例并行执行也安全；ES 处于降级窗口内时
 * 跳过本轮，恢复后水位未推进的部分会自动补齐。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class ProductIndexReconcileTask {

    private final ProductMapper productMapper;

    private final MerchantMapper merchantMapper;

    private final ProductSearchService productSearchService;

    /** 增量重推单批条数 */
    private static final int BATCH_SIZE = 500;

    /** 增量重推单轮最多处理的批数，防止积压时单轮执行过久 */
    private static final int MAX_BATCHES_PER_ROUND = 20;

    /** 水位回看窗口（分钟）：容忍轮询间隔内的边界重复，重复重推幂等 */
    private static final long WATERMARK_OVERLAP_MINUTES = 2L;

    /** 全量差集巡检：MySQL 游标与 ES 扫描的页大小 */
    private static final int ID_SCAN_BATCH = 1000;

    /** 全量差集巡检：单轮补齐与清理的数量上限，剩余交由下一轮 */
    private static final int MAX_REPAIR_PER_ROUND = 2000;

    /** 增量水位（进程内，重启后靠差集巡检兜底更早的缺失），初始回看 30 分钟 */
    private volatile LocalDateTime watermark = LocalDateTime.now().minusMinutes(30);

    /** 每 5 分钟增量重推最近变更的商品 */
    @Scheduled(fixedDelay = 5 * 60_000L, initialDelay = 60_000L)
    public void repairRecentChanges() {
        if (!productSearchService.isAvailable()) {
            return;
        }
        try {
            LocalDateTime cursor = watermark.minusMinutes(WATERMARK_OVERLAP_MINUTES);
            int pushed = 0;
            for (int round = 0; round < MAX_BATCHES_PER_ROUND; round++) {
                if (!productSearchService.isAvailable()) {
                    break;
                }
                List<Product> batch = productMapper.selectList(new LambdaQueryWrapper<Product>()
                        .gt(Product::getUpdateTime, cursor)
                        .orderByAsc(Product::getUpdateTime)
                        .last("LIMIT " + BATCH_SIZE));
                if (batch.isEmpty()) {
                    break;
                }
                reindexProducts(batch);
                // 整批写入后仍可用才推进水位：批次内出现失败（进入降级窗口）时本批留待下一轮重推
                if (!productSearchService.isAvailable()) {
                    log.warn("增量重推出现写入失败，{} 条商品留待下一轮重推", batch.size());
                    break;
                }
                cursor = batch.get(batch.size() - 1).getUpdateTime();
                watermark = cursor;
                pushed += batch.size();
            }
            if (pushed > 0) {
                log.info("商品索引增量重推完成，本轮重推 {} 条", pushed);
            }
        } catch (Exception e) {
            log.warn("商品索引增量重推失败，下一轮重试", e);
        }
    }

    /** 每 30 分钟全量比对 MySQL 与 ES 的 ID 差集并修复 */
    @Scheduled(fixedDelay = 30 * 60_000L, initialDelay = 5 * 60_000L)
    public void repairByIdDiff() {
        if (!productSearchService.isAvailable()) {
            return;
        }
        try {
            Set<Long> dbIds = loadAllProductIds();
            Set<Long> esIds = productSearchService.listIndexedIds();
            if (esIds == null) {
                return;
            }
            int repaired = reindexMissing(dbIds, esIds);
            int removed = removeGhosts(dbIds, esIds);
            if (repaired > 0 || removed > 0) {
                log.info("商品索引差集巡检完成，补齐缺失 {} 条、清理幽灵 {} 条", repaired, removed);
            }
        } catch (Exception e) {
            log.warn("商品索引差集巡检失败，下一轮重试", e);
        }
    }

    /** 补齐 MySQL 有、ES 无的缺失文档（按 ID 回表重推，单轮有数量上限） */
    private int reindexMissing(Set<Long> dbIds, Set<Long> esIds) {
        List<Long> missing = dbIds.stream()
                .filter(id -> !esIds.contains(id))
                .limit(MAX_REPAIR_PER_ROUND)
                .collect(Collectors.toList());
        int repaired = 0;
        for (int i = 0; i < missing.size(); i += BATCH_SIZE) {
            if (!productSearchService.isAvailable()) {
                break;
            }
            List<Long> batch = missing.subList(i, Math.min(i + BATCH_SIZE, missing.size()));
            reindexProducts(productMapper.selectBatchIds(batch));
            repaired += batch.size();
        }
        return repaired;
    }

    /** 清理 ES 有、MySQL 无的幽灵文档（商品已删除但索引删除消息丢失/失败） */
    private int removeGhosts(Set<Long> dbIds, Set<Long> esIds) {
        List<Long> ghosts = esIds.stream()
                .filter(id -> !dbIds.contains(id))
                .limit(MAX_REPAIR_PER_ROUND)
                .collect(Collectors.toList());
        int removed = 0;
        for (Long ghost : ghosts) {
            if (!productSearchService.isAvailable()) {
                break;
            }
            productSearchService.removeProduct(ghost);
            removed++;
        }
        return removed;
    }

    /** 批量回表店铺名后重推一批商品索引（与索引消费者同一套回表逻辑） */
    private void reindexProducts(List<Product> products) {
        if (products.isEmpty()) {
            return;
        }
        Set<Long> merchantIds = products.stream().map(Product::getMerchantId).collect(Collectors.toSet());
        Map<Long, String> shopNames = merchantMapper.selectBatchIds(merchantIds).stream()
                .collect(Collectors.toMap(Merchant::getId,
                        merchant -> merchant.getShopName() == null ? "" : merchant.getShopName()));
        for (Product product : products) {
            productSearchService.indexProduct(product, shopNames.getOrDefault(product.getMerchantId(), ""));
        }
    }

    /** 游标分批拉取 MySQL 全部商品ID（逻辑删除的自动排除） */
    private Set<Long> loadAllProductIds() {
        Set<Long> ids = new HashSet<>();
        long lastId = 0L;
        while (true) {
            List<Product> batch = productMapper.selectList(new LambdaQueryWrapper<Product>()
                    .select(Product::getId)
                    .gt(Product::getId, lastId)
                    .orderByAsc(Product::getId)
                    .last("LIMIT " + ID_SCAN_BATCH));
            if (batch.isEmpty()) {
                return ids;
            }
            for (Product product : batch) {
                ids.add(product.getId());
            }
            lastId = batch.get(batch.size() - 1).getId();
        }
    }
}
