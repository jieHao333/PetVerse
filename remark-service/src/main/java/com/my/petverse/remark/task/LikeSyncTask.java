package com.my.petverse.remark.task;

import com.my.petverse.common.enums.LikeTargetType;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.List;
import java.util.Set;

/**
 * 点赞同步定时任务：每 30 秒消费各对象类型的脏标记，
 * 将 Redis 中的最新点赞集合增量回写 MySQL，保证最终一致
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class LikeSyncTask {

    private final StringRedisTemplate redis;

    private final LikeDbSyncer likeDbSyncer;

    /** 点赞用户集合 Key：like:{targetType}:{targetId} */
    private static final String SET_KEY = "like:%d:%d";

    /** 脏标记集合 Key：like:dirty:{targetType} */
    private static final String DIRTY_KEY = "like:dirty:%d";

    /** 单轮最多处理的脏对象数量 */
    private static final int BATCH_SIZE = 500;

    @Scheduled(fixedDelay = 30_000)
    public void syncDirtyLikes() {
        for (LikeTargetType type : LikeTargetType.values()) {
            try {
                syncType(type.getCode());
            } catch (Exception e) {
                log.error("同步点赞脏数据失败，targetType={}", type.getCode(), e);
            }
        }
    }

    /** 消费指定类型的脏标记集合，逐个对象落库 */
    private void syncType(int targetType) {
        String dirtyKey = String.format(DIRTY_KEY, targetType);
        // SPOP 原子弹出，弹出后新写入的脏标记会留待下一轮，不会丢
        List<String> dirtyIds = redis.opsForSet().pop(dirtyKey, BATCH_SIZE);
        if (dirtyIds == null || dirtyIds.isEmpty()) {
            return;
        }
        for (String idStr : dirtyIds) {
            Long targetId = Long.valueOf(idStr);
            try {
                String setKey = String.format(SET_KEY, targetType, targetId);
                Set<String> redisUsers = redis.opsForSet().members(setKey);
                if (redisUsers == null) {
                    redisUsers = Collections.emptySet();
                }
                likeDbSyncer.syncTarget(targetType, targetId, redisUsers);
            } catch (Exception e) {
                // 落库失败将脏标记放回，下一轮重试，避免变更丢失
                log.error("点赞落库失败，targetType={}, targetId={}", targetType, targetId, e);
                redis.opsForSet().add(dirtyKey, idStr);
            }
        }
    }
}
