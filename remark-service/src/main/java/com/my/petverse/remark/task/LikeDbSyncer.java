package com.my.petverse.remark.task;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.my.petverse.common.entity.remark.LikeRecord;
import com.my.petverse.remark.mapper.LikeCountMapper;
import com.my.petverse.remark.mapper.LikeRecordMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 点赞数据落库执行器，单对象维度事务：
 * Redis 点赞集合与 like_record 增量 diff，并覆盖 like_count 快照。
 * 差异明细按批次拼多条 VALUES / IN 语句写入，避免逐条往返数据库
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class LikeDbSyncer {

    private final LikeRecordMapper likeRecordMapper;

    private final LikeCountMapper likeCountMapper;

    /** 单条 SQL 携带的最大记录数，控制语句长度与事务体积 */
    private static final int BATCH_SIZE = 500;

    /** 以 Redis 集合为准，事务内增量同步单个对象的点赞明细与计数 */
    @Transactional(rollbackFor = Exception.class)
    public void syncTarget(int targetType, Long targetId, Set<String> redisUserIds) {
        Set<Long> redisUsers = redisUserIds.stream()
                .map(Long::valueOf)
                .collect(Collectors.toSet());

        // 只取用户ID列，避免把整行明细读进内存
        Set<Long> dbUsers = likeRecordMapper.selectList(new LambdaQueryWrapper<LikeRecord>()
                        .eq(LikeRecord::getTargetType, targetType)
                        .eq(LikeRecord::getTargetId, targetId)
                        .select(LikeRecord::getUserId))
                .stream()
                .map(LikeRecord::getUserId)
                .collect(Collectors.toSet());

        // 新增的点赞批量插入
        List<LikeRecord> inserted = new ArrayList<>();
        for (Long userId : redisUsers) {
            if (!dbUsers.contains(userId)) {
                LikeRecord record = new LikeRecord();
                record.setId(IdWorker.getId());
                record.setTargetType(targetType);
                record.setTargetId(targetId);
                record.setUserId(userId);
                inserted.add(record);
            }
        }
        for (List<LikeRecord> batch : partition(inserted)) {
            likeRecordMapper.insertBatch(batch);
        }

        // 取消的点赞批量物理删除
        List<Long> removed = dbUsers.stream()
                .filter(userId -> !redisUsers.contains(userId))
                .collect(Collectors.toList());
        for (List<Long> batch : partition(removed)) {
            likeRecordMapper.physicalDeleteBatch(targetType, targetId, batch);
        }

        // 计数快照以 Redis 集合大小为准（绝对值覆盖，失败后下一轮自动修正）
        likeCountMapper.upsert(targetType, targetId, redisUsers.size());
    }

    /** 按批次大小切分，空集合返回空列表 */
    private <T> List<List<T>> partition(Collection<T> source) {
        List<List<T>> batches = new ArrayList<>();
        if (source == null || source.isEmpty()) {
            return batches;
        }
        List<T> current = new ArrayList<>(BATCH_SIZE);
        for (T item : source) {
            current.add(item);
            if (current.size() == BATCH_SIZE) {
                batches.add(current);
                current = new ArrayList<>(BATCH_SIZE);
            }
        }
        if (!current.isEmpty()) {
            batches.add(current);
        }
        return batches;
    }
}