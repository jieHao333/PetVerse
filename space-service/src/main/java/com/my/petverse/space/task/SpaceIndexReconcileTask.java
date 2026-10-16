package com.my.petverse.space.task;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.space.mapper.SpaceMapper;
import com.my.petverse.space.search.SpaceSearchService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 动态索引对账补偿任务：索引维护由 MQ 异步驱动，消息丢失或消费时写入被降级逻辑
 * 吞掉（不会触发 MQ 重试）会导致 ES 文档缺失或内容滞后，本任务以 MySQL 为准兜底：
 * 1）增量重推：按 update_time 水位重推最近变更的动态（每 5 分钟一轮，快速修复近期写入失败）；
 * 2）差集巡检：定期全量比对 MySQL 与 ES 的 ID 差集，补齐缺失文档、清理已删除动态的幽灵文档（每 30 分钟一轮）。
 * 重推与删除均幂等（重推前回表读最新数据），多实例并行执行也安全；ES 处于降级窗口内时
 * 跳过本轮，恢复后水位未推进的部分会自动补齐。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class SpaceIndexReconcileTask {

    private final SpaceMapper spaceMapper;

    private final SpaceSearchService spaceSearchService;

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

    /** 每 5 分钟增量重推最近变更的动态 */
    @Scheduled(fixedDelay = 5 * 60_000L, initialDelay = 60_000L)
    public void repairRecentChanges() {
        if (!spaceSearchService.isAvailable()) {
            return;
        }
        try {
            LocalDateTime cursor = watermark.minusMinutes(WATERMARK_OVERLAP_MINUTES);
            int pushed = 0;
            for (int round = 0; round < MAX_BATCHES_PER_ROUND; round++) {
                if (!spaceSearchService.isAvailable()) {
                    break;
                }
                List<Space> batch = spaceMapper.selectList(new LambdaQueryWrapper<Space>()
                        .gt(Space::getUpdateTime, cursor)
                        .orderByAsc(Space::getUpdateTime)
                        .last("LIMIT " + BATCH_SIZE));
                if (batch.isEmpty()) {
                    break;
                }
                for (Space space : batch) {
                    spaceSearchService.indexSpace(space);
                }
                // 整批写入后仍可用才推进水位：批次内出现失败（进入降级窗口）时本批留待下一轮重推
                if (!spaceSearchService.isAvailable()) {
                    log.warn("增量重推出现写入失败，{} 条动态留待下一轮重推", batch.size());
                    break;
                }
                cursor = batch.get(batch.size() - 1).getUpdateTime();
                watermark = cursor;
                pushed += batch.size();
            }
            if (pushed > 0) {
                log.info("动态索引增量重推完成，本轮重推 {} 条", pushed);
            }
        } catch (Exception e) {
            log.warn("动态索引增量重推失败，下一轮重试", e);
        }
    }

    /** 每 30 分钟全量比对 MySQL 与 ES 的 ID 差集并修复 */
    @Scheduled(fixedDelay = 30 * 60_000L, initialDelay = 5 * 60_000L)
    public void repairByIdDiff() {
        if (!spaceSearchService.isAvailable()) {
            return;
        }
        try {
            Set<Long> dbIds = loadAllSpaceIds();
            Set<Long> esIds = spaceSearchService.listIndexedIds();
            if (esIds == null) {
                return;
            }
            int repaired = reindexMissing(dbIds, esIds);
            int removed = removeGhosts(dbIds, esIds);
            if (repaired > 0 || removed > 0) {
                log.info("动态索引差集巡检完成，补齐缺失 {} 条、清理幽灵 {} 条", repaired, removed);
            }
        } catch (Exception e) {
            log.warn("动态索引差集巡检失败，下一轮重试", e);
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
            if (!spaceSearchService.isAvailable()) {
                break;
            }
            List<Long> batch = missing.subList(i, Math.min(i + BATCH_SIZE, missing.size()));
            for (Space space : spaceMapper.selectBatchIds(batch)) {
                spaceSearchService.indexSpace(space);
            }
            repaired += batch.size();
        }
        return repaired;
    }

    /** 清理 ES 有、MySQL 无的幽灵文档（动态已删除但索引删除消息丢失/失败） */
    private int removeGhosts(Set<Long> dbIds, Set<Long> esIds) {
        List<Long> ghosts = esIds.stream()
                .filter(id -> !dbIds.contains(id))
                .limit(MAX_REPAIR_PER_ROUND)
                .collect(Collectors.toList());
        int removed = 0;
        for (Long ghost : ghosts) {
            if (!spaceSearchService.isAvailable()) {
                break;
            }
            spaceSearchService.removeSpace(ghost);
            removed++;
        }
        return removed;
    }

    /** 游标分批拉取 MySQL 全部动态ID（逻辑删除的自动排除） */
    private Set<Long> loadAllSpaceIds() {
        Set<Long> ids = new HashSet<>();
        long lastId = 0L;
        while (true) {
            List<Space> batch = spaceMapper.selectList(new LambdaQueryWrapper<Space>()
                    .select(Space::getId)
                    .gt(Space::getId, lastId)
                    .orderByAsc(Space::getId)
                    .last("LIMIT " + ID_SCAN_BATCH));
            if (batch.isEmpty()) {
                return ids;
            }
            for (Space space : batch) {
                ids.add(space.getId());
            }
            lastId = batch.get(batch.size() - 1).getId();
        }
    }
}
