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
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 通用点赞服务实现
 * Redis 为实时读写层：Set 存点赞用户（like:{type}:{targetId}），
 * 脏标记（like:dirty:{type}）由定时任务消费后落库
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

    @Override
    public void like(Integer targetType, Long targetId, Long userId) {
        checkTargetType(targetType);
        ensureLoaded(targetType, targetId);
        String setKey = setKey(targetType, targetId);
        Long added = redis.opsForSet().add(setKey, String.valueOf(userId));
        // 新增成功才标记脏数据并发布变更事件，重复点赞无需同步；
        // 事件携带变更后的最新计数，业务侧据此近实时刷新热度冗余列（定时任务仍作全量兜底）
        if (added != null && added == 1L) {
            redis.opsForSet().add(dirtyKey(targetType), String.valueOf(targetId));
            publishLikeChanged(targetType, targetId, setKey, userId, true);
        }
    }

    @Override
    public void unlike(Integer targetType, Long targetId, Long userId) {
        checkTargetType(targetType);
        ensureLoaded(targetType, targetId);
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
        Map<Long, Long> counts = new HashMap<>();
        Set<Long> likedIds = new HashSet<>();
        for (Long targetId : targetIds) {
            ensureLoaded(targetType, targetId);
            String setKey = setKey(targetType, targetId);
            Long size = redis.opsForSet().size(setKey);
            counts.put(targetId, size == null ? 0L : size);
            Boolean isMember = redis.opsForSet().isMember(setKey, String.valueOf(userId));
            if (Boolean.TRUE.equals(isMember)) {
                likedIds.add(targetId);
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
        Map<Long, Long> counts = new HashMap<>();
        for (Long targetId : targetIds) {
            ensureLoaded(targetType, targetId);
            Long size = redis.opsForSet().size(setKey(targetType, targetId));
            counts.put(targetId, size == null ? 0L : size);
        }
        return counts;
    }

    /**
     * 冷数据回填：Redis 中不存在该对象的点赞集合且无待同步脏数据时，
     * 从 like_record 表加载历史点赞用户写回 Redis；
     * 有脏标记时跳过（说明存在未落库的变更，避免用旧数据覆盖）
     */
    private void ensureLoaded(Integer targetType, Long targetId) {
        String setKey = setKey(targetType, targetId);
        try {
            if (Boolean.TRUE.equals(redis.hasKey(setKey))) {
                return;
            }
            Boolean pending = redis.opsForSet().isMember(dirtyKey(targetType), String.valueOf(targetId));
            if (Boolean.TRUE.equals(pending)) {
                return;
            }
            List<LikeRecord> records = likeRecordMapper.selectList(new LambdaQueryWrapper<LikeRecord>()
                    .eq(LikeRecord::getTargetType, targetType)
                    .eq(LikeRecord::getTargetId, targetId));
            if (!records.isEmpty()) {
                String[] userIds = records.stream()
                        .map(r -> String.valueOf(r.getUserId()))
                        .toArray(String[]::new);
                redis.opsForSet().add(setKey, userIds);
            }
        } catch (Exception e) {
            // 回填失败不阻断读写，按空集合处理，下一轮重试
            log.warn("点赞冷数据回填失败，targetType={}, targetId={}", targetType, targetId, e);
        }
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
