package com.my.petverse.user.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.enums.UserRole;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqDeduplicator;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.MerchantApprovedMessage;
import com.my.petverse.user.service.UserService;
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
 * 商户入驻审批通过事件消费者：异步将申请人角色升级为商家。
 * 替代原 shop-service 审批事务内的同步 Feign 调用，升级为覆盖写天然幂等，
 * 另按申请单ID去重防御重复投递；失败由监听容器重试直至成功（最终一致）
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class MerchantRoleConsumer {

    private final UserService userService;

    private final MqDeduplicator deduplicator;

    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = @QueueBinding(
                    value = @Queue(value = MqQueues.USER_ROLE, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE,
                                            value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY,
                                            value = MqQueues.USER_ROLE)}),
                    exchange = @Exchange(value = MqTopics.MERCHANT_EVENT,
                            type = ExchangeTypes.TOPIC, durable = "true"),
                    key = MqTopics.TAG_MERCHANT_APPROVED),
            containerFactory = RabbitMqConfig.LISTENER_FACTORY)
    public void onMessage(Message message) {
        MerchantApprovedMessage msg;
        try {
            msg = objectMapper.readValue(MqMessages.body(message), MerchantApprovedMessage.class);
        } catch (Exception e) {
            log.error("解析商户审批通过事件消息失败，msgId={}", MqMessages.messageId(message), e);
            return;
        }
        if (msg.getUserId() == null || msg.getApplyId() == null) {
            return;
        }
        String dedupKey = "merchant-approved:" + msg.getApplyId();
        if (!deduplicator.firstTime(dedupKey)) {
            return;
        }
        try {
            userService.upgradeRole(msg.getUserId(), UserRole.MERCHANT.name());
            log.info("商家入驻审批通过，用户角色已升级为 MERCHANT，userId={}", msg.getUserId());
        } catch (BusinessException e) {
            // 业务异常（如用户已注销）不可通过重试恢复，记录日志后正常消费，避免无限重试
            log.warn("升级商家角色被业务拒绝，不再重试，userId={}, msg={}", msg.getUserId(), e.getMessage());
        } catch (Exception e) {
            deduplicator.release(dedupKey);
            log.error("升级商家角色失败，等待重试，userId={}", msg.getUserId(), e);
            throw e;
        }
    }
}
