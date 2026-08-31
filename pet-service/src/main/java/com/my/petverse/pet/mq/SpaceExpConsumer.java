package com.my.petverse.pet.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.enums.PetExpSource;
import com.my.petverse.common.mq.MqDeduplicator;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.SpaceCreatedMessage;
import com.my.petverse.pet.service.PetService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;

/**
 * 动态发布事件消费者：异步为发布人的出场宠物发放经验奖励。
 * 替代原 space-service 事务内同步 Feign 调用，解耦发布主流程；
 * 消费失败（如 pet-service 瞬时异常）由 RocketMQ 自动重试，按动态ID去重保证不重复发放
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.SPACE_EVENT,
        selectorExpression = MqTopics.TAG_SPACE_CREATED,
        consumerGroup = "pet-exp-consumer")
public class SpaceExpConsumer implements RocketMQListener<MessageExt> {

    private final PetService petService;

    private final MqDeduplicator deduplicator;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        SpaceCreatedMessage msg;
        try {
            msg = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    SpaceCreatedMessage.class);
        } catch (Exception e) {
            log.error("解析动态发布事件消息失败，msgId={}", messageExt.getMsgId(), e);
            return;
        }
        if (msg.getSpaceId() == null || msg.getUserId() == null) {
            return;
        }
        // 至少投递一次语义下按动态ID去重，避免重复发放经验
        if (!deduplicator.firstTime("space-created:" + msg.getSpaceId())) {
            return;
        }
        try {
            PetExpGrantDTO dto = new PetExpGrantDTO();
            dto.setUserId(msg.getUserId());
            dto.setSource(PetExpSource.NOTE.name());
            // 用户无出场宠物时返回 null，属正常情况
            petService.grantExp(dto);
        } catch (Exception e) {
            // 释放去重键并抛出异常，交由 RocketMQ 重试，避免重试消息被去重器拦截
            deduplicator.release("space-created:" + msg.getSpaceId());
            log.error("消费动态发布事件发放宠物经验失败，spaceId={}, userId={}",
                    msg.getSpaceId(), msg.getUserId(), e);
            throw e;
        }
    }
}
