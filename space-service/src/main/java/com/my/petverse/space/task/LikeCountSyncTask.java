package com.my.petverse.space.task;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.enums.LikeTargetType;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.space.feign.RemarkFeignClient;
import com.my.petverse.space.mapper.SpaceMapper;
import com.my.petverse.space.search.SpaceSearchService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 点赞数同步任务：定时从点赞服务拉取动态点赞数，
 * 更新 space.like_count 冗余列，支撑热度排序
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class LikeCountSyncTask {

    private final SpaceMapper spaceMapper;

    private final RemarkFeignClient remarkFeignClient;

    private final SpaceSearchService spaceSearchService;

    /** 单轮拉取的动态数量上限 */
    private static final int SYNC_LIMIT = 1000;

    /** 每 60 秒拉取一次点赞数 */
    @Scheduled(fixedDelay = 60_000, initialDelay = 10_000)
    public void syncLikeCounts() {
        try {
            List<Space> spaces = spaceMapper.selectList(new LambdaQueryWrapper<Space>()
                    .select(Space::getId, Space::getLikeCount)
                    .orderByDesc(Space::getCreateTime)
                    .last("LIMIT " + SYNC_LIMIT));
            List<Long> ids = spaces.stream().map(Space::getId).collect(Collectors.toList());
            if (ids.isEmpty()) {
                return;
            }
            String idStr = ids.stream().map(String::valueOf).collect(Collectors.joining(","));
            Result<Map<Long, Long>> result = remarkFeignClient.counts(LikeTargetType.SPACE.getCode(), idStr);
            if (result == null || result.getCode() != ResultCode.SUCCESS.getCode() || result.getData() == null) {
                return;
            }
            Map<Long, Long> counts = result.getData();
            for (Space space : spaces) {
                int count = counts.getOrDefault(space.getId(), 0L).intValue();
                // 点赞数未变时跳过，避免每轮无谓刷库与刷索引
                if (space.getLikeCount() != null && space.getLikeCount() == count) {
                    continue;
                }
                spaceMapper.update(null, new LambdaUpdateWrapper<Space>()
                        .eq(Space::getId, space.getId())
                        .set(Space::getLikeCount, count));
                spaceSearchService.updateLikeCount(space.getId(), count);
            }
        } catch (Exception e) {
            log.warn("同步点赞数到 space.like_count 失败，下一轮重试", e);
        }
    }
}
