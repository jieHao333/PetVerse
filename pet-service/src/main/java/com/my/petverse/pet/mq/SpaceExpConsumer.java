package com.my.petverse.pet.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.enums.PetExpSource;
import com.my.petverse.common.mq.MqDeduplicator;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.SpaceCreatedMessage;
import com.my.petverse.pet.service.PetService;
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
 * 动态发布事件消费者：异步为发布人的虚拟宠物发放经验奖励。
 * 替代原 space-service 事务内同步 Feign 调用，解耦发布主流程；
 * 消费失败（如 pet-service 瞬时异常）由监听容器自动重试，按动态ID去重保证不重复发放
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class SpaceExpConsumer {

    private final PetService petService;

    private final MqDeduplicator deduplicator;

    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = @QueueBinding(
                    value = @Queue(value = MqQueues.PET_EXP, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE,
                                            value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY,
                                            value = MqQueues.PET_EXP)}),
                    exchange = @Exchange(value = MqTopics.SPACE_EVENT,
                            type = ExchangeTypes.TOPIC, durable = "true"),
                    key = MqTopics.TAG_SPACE_CREATED),
            containerFactory = RabbitMqConfig.LISTENER_FACTORY)
    public void onMessage(Message message) {
        SpaceCreatedMessage msg;
        try {
            msg = objectMapper.readValue(MqMessages.body(message), SpaceCreatedMessage.class);
        } catch (Exception e) {
            log.error("解析动态发布事件消息失败，msgId={}", MqMessages.messageId(message), e);
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
            // 用户无虚拟宠物时返回 null，属正常情况
            petService.grantExp(dto);
        } catch (Exception e) {
            // 释放去重键并抛出异常，交由监听容器重试，避免重试消息被去重器拦截
            deduplicator.release("space-created:" + msg.getSpaceId());
            log.error("消费动态发布事件发放宠物经验失败，spaceId={}, userId={}",
                    msg.getSpaceId(), msg.getUserId(), e);
            throw e;
        }
    }
}
