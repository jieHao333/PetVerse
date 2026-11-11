package com.my.petverse.space.mq;

import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.enums.LikeTargetType;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.LikeChangedMessage;
import com.my.petverse.space.mapper.SpaceMapper;
import com.my.petverse.space.search.SpaceSearchService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.ExchangeTypes;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.Argument;
import org.springframework.amqp.rabbit.annotation.Exchange;
import org.springframework.amqp.rabbit.annotation.Queue;
import org.springframework.amqp.rabbit.annotation.QueueBinding;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * 点赞变更事件消费者：近实时更新 space.like_count 冗余列与 ES 热度，
 * 替代纯定时拉取的分钟级延迟；写入为整列覆盖，重复消费幂等，
 * 定时同步任务保留作最终一致兜底
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class LikeCountConsumer {

    private final SpaceMapper spaceMapper;

    private final SpaceSearchService spaceSearchService;

    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = @QueueBinding(
                    value = @Queue(value = MqQueues.SPACE_LIKE_COUNT, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE,
                                            value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY,
                                            value = MqQueues.SPACE_LIKE_COUNT)}),
                    exchange = @Exchange(value = MqTopics.LIKE_CHANGED,
                            type = ExchangeTypes.TOPIC, durable = "true"),
                    key = MqTopics.TAG_LIKE_CHANGED),
            containerFactory = RabbitMqConfig.SHORT_RETRY_LISTENER_FACTORY)
    public void onMessage(Message message) {
        LikeChangedMessage msg;
        try {
            msg = objectMapper.readValue(MqMessages.body(message), LikeChangedMessage.class);
        } catch (Exception e) {
            log.error("解析点赞变更事件消息失败，msgId={}", MqMessages.messageId(message), e);
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
