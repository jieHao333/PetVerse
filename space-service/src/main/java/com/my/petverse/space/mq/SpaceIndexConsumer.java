package com.my.petverse.space.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.SpaceIndexMessage;
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
 * 动态检索索引消费者：异步维护 Elasticsearch 索引，替代原发布/修改/删除路径上的同步双写，
 * 避免 ES 抖动拖慢主流程；索引操作以ID回表读取最新数据，天然幂等，失败由监听容器重试
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class SpaceIndexConsumer {

    private final SpaceMapper spaceMapper;

    private final SpaceSearchService spaceSearchService;

    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = @QueueBinding(
                    value = @Queue(value = MqQueues.SPACE_INDEX, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE,
                                            value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY,
                                            value = MqQueues.SPACE_INDEX)}),
                    exchange = @Exchange(value = MqTopics.SPACE_EVENT,
                            type = ExchangeTypes.TOPIC, durable = "true"),
                    key = {MqTopics.TAG_SPACE_INDEX_UPSERT, MqTopics.TAG_SPACE_INDEX_REMOVE}),
            containerFactory = RabbitMqConfig.LISTENER_FACTORY)
    public void onMessage(Message message) {
        SpaceIndexMessage msg;
        try {
            msg = objectMapper.readValue(MqMessages.body(message), SpaceIndexMessage.class);
        } catch (Exception e) {
            log.error("解析动态索引事件消息失败，msgId={}", MqMessages.messageId(message), e);
            return;
        }
        if (msg.getSpaceId() == null) {
            return;
        }
        try {
            if (MqTopics.TAG_SPACE_INDEX_REMOVE.equals(MqMessages.tag(message))) {
                spaceSearchService.removeSpace(msg.getSpaceId());
                return;
            }
            // upsert：回表读取最新数据刷索引；动态已删除时忽略
            Space space = spaceMapper.selectById(msg.getSpaceId());
            if (space == null) {
                log.warn("索引事件对应的动态不存在（可能已删除），spaceId={}", msg.getSpaceId());
                return;
            }
            spaceSearchService.indexSpace(space);
        } catch (Exception e) {
            log.error("维护动态检索索引失败，spaceId={}", msg.getSpaceId(), e);
            throw e;
        }
    }
}
