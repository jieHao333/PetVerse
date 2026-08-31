package com.my.petverse.common.mq;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.spring.core.RocketMQTemplate;
import org.apache.rocketmq.spring.support.RocketMQHeaders;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.messaging.support.MessageBuilder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * 统一消息发布器，屏蔽 RocketMQ 细节：
 * 1. 未配置 rocketmq.name-server 时 RocketMQTemplate 不存在，发送自动降级为仅记日志，不影响业务；
 * 2. 发送失败仅记日志不抛异常，保证主流程不被消息中间件拖垮；
 * 3. 提供事务提交后发送能力，避免"消息已发、事务回滚"导致的脏事件
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class MqEventPublisher {

    /** 同步发送超时（毫秒） */
    private static final long SEND_TIMEOUT_MS = 3000L;

    /** RocketMQTemplate 仅在配置了 rocketmq.name-server 时由 starter 装配 */
    private final ObjectProvider<RocketMQTemplate> templateProvider;

    /** 是否已启用 RocketMQ（配置了 name-server 且生产者已装配） */
    public boolean isEnabled() {
        return templateProvider.getIfAvailable() != null;
    }

    /** 立即发送消息 */
    public void publish(String topic, String tag, Object payload) {
        doSend(destination(topic, tag), payload, null, null);
    }

    /** 立即发送消息并携带业务去重键（消费端据此幂等） */
    public void publish(String topic, String tag, Object payload, String key) {
        doSend(destination(topic, tag), payload, key, null);
    }

    /** 发送延迟消息，delayLevel 为开源版固定档位（如 14=10分钟） */
    public void publishDelayed(String topic, String tag, Object payload, String key, int delayLevel) {
        doSend(destination(topic, tag), payload, key, delayLevel);
    }

    /** 事务提交后发送；无活跃事务时立即发送 */
    public void publishAfterCommit(String topic, String tag, Object payload, String key) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    publish(topic, tag, payload, key);
                }
            });
        } else {
            publish(topic, tag, payload, key);
        }
    }

    /** 事务提交后发送延迟消息；无活跃事务时立即发送 */
    public void publishDelayedAfterCommit(String topic, String tag, Object payload, String key, int delayLevel) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    publishDelayed(topic, tag, payload, key, delayLevel);
                }
            });
        } else {
            publishDelayed(topic, tag, payload, key, delayLevel);
        }
    }

    private void doSend(String destination, Object payload, String key, Integer delayLevel) {
        RocketMQTemplate template = templateProvider.getIfAvailable();
        if (template == null) {
            log.warn("RocketMQ 未启用（缺少 rocketmq.name-server 配置），跳过消息发送：{}", destination);
            return;
        }
        try {
            MessageBuilder<Object> builder = MessageBuilder.withPayload(payload);
            if (key != null) {
                builder.setHeader(RocketMQHeaders.KEYS, key);
            }
            if (delayLevel == null) {
                template.syncSend(destination, builder.build(), SEND_TIMEOUT_MS);
            } else {
                template.syncSend(destination, builder.build(), SEND_TIMEOUT_MS, delayLevel);
            }
        } catch (Exception e) {
            log.error("消息发送失败，destination={}", destination, e);
        }
    }

    private String destination(String topic, String tag) {
        return topic + ":" + tag;
    }
}
