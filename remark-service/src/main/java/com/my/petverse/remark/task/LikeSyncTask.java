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

    /** 单轮最多处理的脏对象数量：限制单轮数据库写入量，剩余脏标记留待下一轮 */
    private static final int MAX_TARGETS_PER_ROUND = 200;

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

    /** 消费指定类型的脏标记集合，逐个对象落库；失败的对象放回脏标记集合下轮重试 */
    private void syncType(int targetType) {
        String dirtyKey = String.format(DIRTY_KEY, targetType);
        // SPOP 原子弹出：本轮未取到的脏标记留在集合中由下一轮处理，不会丢
        List<String> dirtyIds = redis.opsForSet().pop(dirtyKey, MAX_TARGETS_PER_ROUND);
        if (dirtyIds == null || dirtyIds.isEmpty()) {
            return;
        }
        int synced = 0;
        for (String idStr : dirtyIds) {
            Long targetId = Long.valueOf(idStr);
            try {
                String setKey = String.format(SET_KEY, targetType, targetId);
                Set<String> redisUsers = redis.opsForSet().members(setKey);
                if (redisUsers == null) {
                    redisUsers = Collections.emptySet();
                }
                likeDbSyncer.syncTarget(targetType, targetId, redisUsers);
                synced++;
            } catch (Exception e) {
                // 落库失败将脏标记放回，下一轮重试，避免变更丢失
                log.error("点赞落库失败，targetType={}, targetId={}", targetType, targetId, e);
                redis.opsForSet().add(dirtyKey, idStr);
            }
        }
        log.info("点赞落库完成，targetType={}, 成功={}, 本轮取出={}", targetType, synced, dirtyIds.size());
    }
}