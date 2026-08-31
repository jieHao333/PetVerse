package com.my.petverse.user.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.enums.UserRole;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqDeduplicator;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.MerchantApprovedMessage;
import com.my.petverse.user.service.UserService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;

/**
 * 商户入驻审批通过事件消费者：异步将申请人角色升级为商家。
 * 替代原 shop-service 审批事务内的同步 Feign 调用，升级为覆盖写天然幂等，
 * 另按申请单ID去重防御重复投递；失败由 RocketMQ 重试直至成功（最终一致）
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.MERCHANT_EVENT,
        selectorExpression = MqTopics.TAG_MERCHANT_APPROVED,
        consumerGroup = "user-role-consumer")
public class MerchantRoleConsumer implements RocketMQListener<MessageExt> {

    private final UserService userService;

    private final MqDeduplicator deduplicator;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        MerchantApprovedMessage msg;
        try {
            msg = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    MerchantApprovedMessage.class);
        } catch (Exception e) {
            log.error("解析商户审批通过事件消息失败，msgId={}", messageExt.getMsgId(), e);
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
