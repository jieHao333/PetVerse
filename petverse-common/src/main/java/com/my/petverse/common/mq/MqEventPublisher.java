package com.my.petverse.common.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.ReturnedMessage;
import org.springframework.amqp.rabbit.connection.CachingConnectionFactory;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.UUID;
import java.util.concurrent.TimeUnit;

/**
 * 统一消息发布器，屏蔽 RabbitMQ 细节：
 * 1. 未启用 MQ（petverse.mq.enabled=false）或 RabbitTemplate 未装配时，发送自动降级为仅记日志，不影响业务；
 * 2. 发送失败仅记日志不抛异常，保证主流程不被消息中间件拖垮；
 *    需要保证事件不丢的业务可用带兜底回调的 publishAfterCommit 重载；
 * 3. 提供事务提交后发送能力，避免"消息已发、事务回滚"导致的脏事件；
 * 4. 投递语义对齐原 RocketMQ 同步发送：等待 Broker 发布确认，并校验 mandatory 退回，
 *    只有真正落到队列才返回 true。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class MqEventPublisher {

    /** 同步发送超时（毫秒）：等待 Broker 发布确认的最长时间 */
    private static final long SEND_TIMEOUT_MS = 3000L;

    /** RabbitTemplate 由 spring-boot-starter-amqp 装配，未引入或已排除时为空 */
    private final ObjectProvider<RabbitTemplate> templateProvider;

    private final RabbitMqDelaySupport delaySupport;

    private final ObjectMapper objectMapper;

    /** MQ 总开关：为 false 时消息发送直接失败，触发调用方注册的本地降级逻辑 */
    @Value("${petverse.mq.enabled:true}")
    private boolean mqEnabled;

    /** 是否已装配 MQ 发布器（RabbitTemplate 存在即可用；总开关仅影响实际发送，见 doSend） */
    public boolean isEnabled() {
        return templateProvider.getIfAvailable() != null;
    }

    /** 立即发送消息；返回是否真正投递成功（未启用 MQ 或发送失败返回 false） */
    public boolean publish(String exchange, String routingKey, Object payload) {
        return doSend(exchange, routingKey, payload, null, null);
    }

    /** 立即发送消息并携带业务去重键（消费端据此幂等） */
    public boolean publish(String exchange, String routingKey, Object payload, String key) {
        return doSend(exchange, routingKey, payload, key, null);
    }

    /** 发送延迟消息，delayLevel 沿用 RocketMQ 开源版固定档位（如 14=10分钟） */
    public boolean publishDelayed(String exchange, String routingKey, Object payload, String key, int delayLevel) {
        return doSend(exchange, routingKey, payload, key, delayLevel);
    }

    /** 事务提交后发送；无活跃事务时立即发送 */
    public void publishAfterCommit(String exchange, String routingKey, Object payload, String key) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    publish(exchange, routingKey, payload, key);
                }
            });
        } else {
            publish(exchange, routingKey, payload, key);
        }
    }

    /**
     * 事务提交后发送；未启用 MQ 或投递失败时执行 fallback。
     * 单体版各模块同进程共存，可据此把跨服务事件降级为本地直接调用，避免事件丢失导致业务状态不一致
     */
    public void publishAfterCommit(String exchange, String routingKey, Object payload, String key, Runnable fallback) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    if (!publish(exchange, routingKey, payload, key)) {
                        fallback.run();
                    }
                }
            });
        } else if (!publish(exchange, routingKey, payload, key)) {
            fallback.run();
        }
    }

    /** 事务提交后发送延迟消息；无活跃事务时立即发送 */
    public void publishDelayedAfterCommit(String exchange, String routingKey, Object payload, String key,
                                          int delayLevel) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    publishDelayed(exchange, routingKey, payload, key, delayLevel);
                }
            });
        } else {
            publishDelayed(exchange, routingKey, payload, key, delayLevel);
        }
    }

    private boolean doSend(String exchange, String routingKey, Object payload, String key, Integer delayLevel) {
        if (!mqEnabled) {
            log.debug("MQ 已禁用（petverse.mq.enabled=false），跳过消息发送：{}/{}", exchange, routingKey);
            return false;
        }
        RabbitTemplate template = templateProvider.getIfAvailable();
        if (template == null) {
            log.warn("RabbitMQ 未启用（缺少 RabbitTemplate），跳过消息发送：{}/{}", exchange, routingKey);
            return false;
        }
        try {
            Message message = MqMessages.build(objectMapper, payload, key, routingKey);
            if (delayLevel == null) {
                return send(template, exchange, routingKey, message);
            }
            // 延迟消息：先投递到 TTL 队列，到期后经死信交换机回到目标 Exchange
            String delayQueue = delaySupport.ensureDelayQueue(exchange, routingKey, delayLevel);
            if (delayQueue == null) {
                return false;
            }
            return send(template, "", delayQueue, message);
        } catch (Exception e) {
            log.error("消息发送失败，exchange={}, routingKey={}", exchange, routingKey, e);
            return false;
        }
    }

    /**
     * 投递并等待发布确认：开启 publisher-confirm 时以 Broker 确认为准，
     * 未开启时退化为只保证消息已写出（与原 RocketMQ 同步发送仍尽力而为）
     */
    private boolean send(RabbitTemplate template, String exchange, String routingKey, Message message)
            throws Exception {
        CorrelationData correlation = new CorrelationData(UUID.randomUUID().toString());
        template.send(exchange, routingKey, message, correlation);
        if (!confirmEnabled(template)) {
            return true;
        }
        CorrelationData.Confirm confirm = correlation.getFuture().get(SEND_TIMEOUT_MS, TimeUnit.MILLISECONDS);
        if (confirm == null || !confirm.isAck()) {
            log.error("消息未被 Broker 确认，exchange={}, routingKey={}, reason={}",
                    exchange, routingKey, confirm == null ? "confirm timeout" : confirm.getReason());
            return false;
        }
        ReturnedMessage returned = correlation.getReturned();
        if (returned != null) {
            log.error("消息无法路由，已被 Broker 退回，exchange={}, routingKey={}, replyText={}",
                    exchange, routingKey, returned.getReplyText());
            return false;
        }
        return true;
    }

    /** 是否开启了发布确认（spring.rabbitmq.publisher-confirm-type=correlated） */
    private boolean confirmEnabled(RabbitTemplate template) {
        return template.getConnectionFactory() instanceof CachingConnectionFactory connectionFactory
                && connectionFactory.isPublisherConfirms();
    }
}
