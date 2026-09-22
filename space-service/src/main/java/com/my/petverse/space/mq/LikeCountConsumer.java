package com.my.petverse.space.mq;

import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.enums.LikeTargetType;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.LikeChangedMessage;
import com.my.petverse.space.mapper.SpaceMapper;
import com.my.petverse.space.search.SpaceSearchService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;

/**
 * 点赞变更事件消费者：近实时更新 space.like_count 冗余列与 ES 热度，
 * 替代纯定时拉取的分钟级延迟；写入为整列覆盖，重复消费幂等，
 * 定时同步任务保留作最终一致兜底
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.LIKE_CHANGED,
        selectorExpression = MqTopics.TAG_LIKE_CHANGED,
        consumerGroup = "space-like-count-consumer",
        consumeThreadMax = 32, maxReconsumeTimes = 5)
public class LikeCountConsumer implements RocketMQListener<MessageExt> {

    private final SpaceMapper spaceMapper;

    private final SpaceSearchService spaceSearchService;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        LikeChangedMessage msg;
        try {
            msg = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    LikeChangedMessage.class);
        } catch (Exception e) {
            log.error("解析点赞变更事件消息失败，msgId={}", messageExt.getMsgId(), e);
            return;
        }
        // 仅处理动态对象的点赞变更，其他类型（如评论）直接忽略
        if (msg.getTargetType() == null
                || !msg.getTargetType().equals(LikeTargetType.SPACE.getCode())
                || msg.getTargetId() == null || msg.getCount() == null) {
            return;
        }
        try {
            int count = msg.getCount().intValue();
            spaceMapper.update(null, new LambdaUpdateWrapper<Space>()
                    .eq(Space::getId, msg.getTargetId())
                    .set(Space::getLikeCount, count));
            spaceSearchService.updateLikeCount(msg.getTargetId(), count);
        } catch (Exception e) {
            log.error("同步点赞数到 space.like_count 失败，spaceId={}", msg.getTargetId(), e);
            throw e;
        }
    }
}
