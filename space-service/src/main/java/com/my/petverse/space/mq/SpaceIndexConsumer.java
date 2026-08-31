package com.my.petverse.space.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.SpaceIndexMessage;
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
 * 动态检索索引消费者：异步维护 Elasticsearch 索引，替代原发布/修改/删除路径上的同步双写，
 * 避免 ES 抖动拖慢主流程；索引操作以ID回表读取最新数据，天然幂等，失败由 RocketMQ 重试
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.SPACE_EVENT,
        selectorExpression = MqTopics.TAG_SPACE_INDEX_UPSERT + " || " + MqTopics.TAG_SPACE_INDEX_REMOVE,
        consumerGroup = "space-index-consumer")
public class SpaceIndexConsumer implements RocketMQListener<MessageExt> {

    private final SpaceMapper spaceMapper;

    private final SpaceSearchService spaceSearchService;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        SpaceIndexMessage msg;
        try {
            msg = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    SpaceIndexMessage.class);
        } catch (Exception e) {
            log.error("解析动态索引事件消息失败，msgId={}", messageExt.getMsgId(), e);
            return;
        }
        if (msg.getSpaceId() == null) {
            return;
        }
        try {
            if (MqTopics.TAG_SPACE_INDEX_REMOVE.equals(messageExt.getTags())) {
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
