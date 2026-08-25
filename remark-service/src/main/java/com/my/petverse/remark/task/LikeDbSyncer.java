package com.my.petverse.remark.task;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.my.petverse.common.entity.remark.LikeRecord;
import com.my.petverse.remark.mapper.LikeCountMapper;
import com.my.petverse.remark.mapper.LikeRecordMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Set;
import java.util.stream.Collectors;

/**
 * 点赞数据落库执行器，单对象维度事务：
 * Redis 点赞集合与 like_record 增量 diff，并覆盖 like_count 快照
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class LikeDbSyncer {

    private final LikeRecordMapper likeRecordMapper;

    private final LikeCountMapper likeCountMapper;

    /** 以 Redis 集合为准，事务内增量同步单个对象的点赞明细与计数 */
    @Transactional(rollbackFor = Exception.class)
    public void syncTarget(int targetType, Long targetId, Set<String> redisUserIds) {
        Set<Long> redisUsers = redisUserIds.stream()
                .map(Long::valueOf)
                .collect(Collectors.toSet());

        Set<Long> dbUsers = likeRecordMapper.selectList(new LambdaQueryWrapper<LikeRecord>()
                        .eq(LikeRecord::getTargetType, targetType)
                        .eq(LikeRecord::getTargetId, targetId))
                .stream()
                .map(LikeRecord::getUserId)
                .collect(Collectors.toSet());

        // 新增的点赞插入
        for (Long userId : redisUsers) {
            if (!dbUsers.contains(userId)) {
                LikeRecord record = new LikeRecord();
                record.setTargetType(targetType);
                record.setTargetId(targetId);
                record.setUserId(userId);
                likeRecordMapper.insert(record);
            }
        }
        // 取消的点赞物理删除
        for (Long userId : dbUsers) {
            if (!redisUsers.contains(userId)) {
                likeRecordMapper.physicalDelete(targetType, targetId, userId);
            }
        }
        // 计数快照以 Redis 集合大小为准（绝对值覆盖，失败后下一轮自动修正）
        likeCountMapper.upsert(targetType, targetId, redisUsers.size());
    }
}
