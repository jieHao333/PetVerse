package com.my.petverse.remark.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.my.petverse.common.entity.remark.LikeRecord;
import com.my.petverse.common.enums.LikeTargetType;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.LikeChangedMessage;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.remark.LikeBatchVO;
import com.my.petverse.remark.mapper.LikeRecordMapper;
import com.my.petverse.remark.service.LikeService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.connection.RedisConnection;
import org.springframework.data.redis.connection.RedisKeyCommands;
import org.springframework.data.redis.connection.RedisSetCommands;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.Consumer;
import java.util.stream.Collectors;

/**
 * 通用点赞服务实现
 * Redis 为实时读写层：Set 存点赞用户（like:{type}:{targetId}），
 * 脏标记（like:dirty:{type}）由定时任务消费后落库。
 * 列表页的批量读取走 pipeline，一次往返取回整页计数与点赞状态
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class LikeServiceImpl implements LikeService {

    private final StringRedisTemplate redis;

    private final LikeRecordMapper likeRecordMapper;

    private final MqEventPublisher mqEventPublisher;

    /** 点赞用户集合 Key：like:{targetType}:{targetId}，成员为 userId */
    private static final String SET_KEY = "like:%d:%d";

    /** 脏标记集合 Key：like:dirty:{targetType}，成员为 targetId */
    private static final String DIRTY_KEY = "like:dirty:%d";

    /** 单次批量查询的对象数量上限，限制 pipeline 命令数与 IN 查询规模 */
    private static final int MAX_BATCH_TARGETS = 200;

    @Override
    public void like(Integer targetType, Long targetId, Long userId) {
        checkTargetType(targetType);
        ensureLoadedBatch(targetType, List.of(targetId));
        String setKey = setKey(targetType, targetId);
        Long added = redis.opsForSet().add(setKey, String.valueOf(userId));
        // 新增成功才标记脏数据并发布变更事件，重复点赞不产生标记与事件；
        // 事件携带变更后的最新计数，业务侧据此近实时刷新热度冗余列（定时任务仍作全量兜底）
        if (added != null && added == 1L) {
            redis.opsForSet().add(dirtyKey(targetType), String.valueOf(targetId));
            publishLikeChanged(targetType, targetId, setKey, userId, true);
        }
    }

    @Override
    public void unlike(Integer targetType, Long targetId, Long userId) {
        checkTargetType(targetType);
        ensureLoadedBatch(targetType, List.of(targetId));
        String setKey = setKey(targetType, targetId);
        Long removed = redis.opsForSet().remove(setKey, String.valueOf(userId));
        if (removed != null && removed == 1L) {
            redis.opsForSet().add(dirtyKey(targetType), String.valueOf(targetId));
            publishLikeChanged(targetType, targetId, setKey, userId, false);
        }
    }

    /** 发布点赞变更事件（携带最新点赞数与触发人、是否新增），发送失败由发布器内部降级记日志 */
    private void publishLikeChanged(Integer targetType, Long targetId, String setKey, Long actorUserId, boolean added) {
        Long size = redis.opsForSet().size(setKey);
        mqEventPublisher.publish(MqTopics.LIKE_CHANGED, MqTopics.TAG_LIKE_CHANGED,
                new LikeChangedMessage(targetType, targetId, size == null ? 0L : size, actorUserId, added));
    }

    @Override
    public LikeBatchVO batchQuery(Integer targetType, List<Long> targetIds, Long userId) {
        checkTargetType(targetType);
        List<Long> ids = limitTargets(targetIds);
        Map<Long, Long> counts = new HashMap<>();
        Set<Long> likedIds = new HashSet<>();
        if (!ids.isEmpty()) {
            ensureLoadedBatch(targetType, ids);
            byte[] rawUserId = raw(String.valueOf(userId));
            // 整页对象的点赞数与当前用户点赞状态一次 pipeline 发出，避免逐条往返 Redis
            List<Object> results = pipeline(connection -> {
                RedisSetCommands sets = connection.setCommands();
                for (Long targetId : ids) {
                    byte[] rawKey = raw(setKey(targetType, targetId));
                    sets.sCard(rawKey);
                    sets.sIsMember(rawKey, rawUserId);
                }
            });
            for (int i = 0; i < ids.size(); i++) {
                Long targetId = ids.get(i);
                counts.put(targetId, toLong(resultAt(results, i * 2)));
                if (Boolean.TRUE.equals(resultAt(results, i * 2 + 1))) {
                    likedIds.add(targetId);
                }
            }
        }
        LikeBatchVO vo = new LikeBatchVO();
        vo.setCounts(counts);
        vo.setLikedIds(likedIds);
        return vo;
    }

    @Override
    public Map<Long, Long> batchCounts(Integer targetType, List<Long> targetIds) {
        checkTargetType(targetType);
        List<Long> ids = limitTargets(targetIds);
        Map<Long, Long> counts = new HashMap<>();
        if (ids.isEmpty()) {
            return counts;
        }
        ensureLoadedBatch(targetType, ids);
        // 只取计数时每个对象一条 SCARD，同样一次 pipeline 完成
        List<Object> results = pipeline(connection -> {
            RedisSetCommands sets = connection.setCommands();
            for (Long targetId : ids) {
                sets.sCard(raw(setKey(targetType, targetId)));
            }
        });
        for (int i = 0; i < ids.size(); i++) {
            counts.put(ids.get(i), toLong(resultAt(results, i)));
        }
        return counts;
    }

    /**
     * 冷数据批量回填：对「Redis 中还没有点赞集合」且「没有待落库脏标记」的对象，
     * 一次查出 like_record 中的历史点赞用户写回 Redis。
     * 有脏标记的对象跳过，避免用旧数据覆盖尚未落库的新变更。
     */
    private void ensureLoadedBatch(Integer targetType, List<Long> targetIds) {
        List<Long> ids = limitTargets(targetIds);
        if (ids.isEmpty()) {
            return;
        }
        try {
            // 1) 一次 pipeline 找出还没有点赞集合的对象
            List<Object> exists = pipeline(connection -> {
                RedisKeyCommands keys = connection.keyCommands();
                for (Long targetId : ids) {
                    keys.exists(raw(setKey(targetType, targetId)));
                }
            });
            List<Long> missing = new ArrayList<>();
            for (int i = 0; i < ids.size(); i++) {
                if (!Boolean.TRUE.equals(resultAt(exists, i))) {
                    missing.add(ids.get(i));
                }
            }
            if (missing.isEmpty()) {
                return;
            }
            // 2) 一次 pipeline 排除存在待落库变更的对象
            byte[] rawDirtyKey = raw(dirtyKey(targetType));
            List<Object> pending = pipeline(connection -> {
                RedisSetCommands sets = connection.setCommands();
                for (Long targetId : missing) {
                    sets.sIsMember(rawDirtyKey, raw(String.valueOf(targetId)));
                }
            });
            List<Long> candidates = new ArrayList<>();
            for (int i = 0; i < missing.size(); i++) {
                if (!Boolean.TRUE.equals(resultAt(pending, i))) {
                    candidates.add(missing.get(i));
                }
            }
            if (candidates.isEmpty()) {
                return;
            }
            // 3) 一条 SQL 取出候选对象的历史点赞用户，按对象分组
            List<LikeRecord> records = likeRecordMapper.selectList(new LambdaQueryWrapper<LikeRecord>()
                    .eq(LikeRecord::getTargetType, targetType)
                    .in(LikeRecord::getTargetId, candidates)
                    .select(LikeRecord::getTargetId, LikeRecord::getUserId));
            Map<Long, List<byte[]>> grouped = new HashMap<>();
            for (LikeRecord record : records) {
                grouped.computeIfAbsent(record.getTargetId(), key -> new ArrayList<>())
                        .add(raw(String.valueOf(record.getUserId())));
            }
            if (grouped.isEmpty()) {
                return;
            }
            // 4) 一次 pipeline 把各组用户写回集合
            pipeline(connection -> {
                RedisSetCommands sets = connection.setCommands();
                grouped.forEach((targetId, userIds) ->
                        sets.sAdd(raw(setKey(targetType, targetId)), userIds.toArray(new byte[0][])));
            });
        } catch (Exception e) {
            // 回填失败不阻断读写，按空集合处理，下一轮重试
            log.warn("点赞冷数据批量回填失败，targetType={}, 对象数={}", targetType, ids.size(), e);
        }
    }

    /** 提交一次 pipeline：回调内排队的命令一次性发送，返回结果按命令顺序排列 */
    private List<Object> pipeline(Consumer<RedisConnection> commands) {
        List<Object> results = redis.executePipelined((RedisCallback<Object>) connection -> {
            commands.accept(connection);
            return null;
        });
        return results == null ? List.of() : results;
    }

    private byte[] raw(String value) {
        return redis.getStringSerializer().serialize(value);
    }

    /** 截断并去重批量对象ID，限制单次 pipeline 与 IN 查询规模 */
    private List<Long> limitTargets(List<Long> targetIds) {
        if (targetIds == null || targetIds.isEmpty()) {
            return List.of();
        }
        return targetIds.stream()
                .filter(Objects::nonNull)
                .distinct()
                .limit(MAX_BATCH_TARGETS)
                .collect(Collectors.toList());
    }

    private Object resultAt(List<Object> results, int index) {
        return index < results.size() ? results.get(index) : null;
    }

    private long toLong(Object value) {
        return value instanceof Number ? ((Number) value).longValue() : 0L;
    }

    /** 校验点赞对象类型 */
    private void checkTargetType(Integer targetType) {
        if (LikeTargetType.of(targetType) == null) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "无效的点赞对象类型");
        }
    }

    private String setKey(Integer targetType, Long targetId) {
        return String.format(SET_KEY, targetType, targetId);
    }

    private String dirtyKey(Integer targetType) {
        return String.format(DIRTY_KEY, targetType);
    }
}