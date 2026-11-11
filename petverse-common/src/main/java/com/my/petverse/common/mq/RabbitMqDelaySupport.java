package com.my.petverse.common.mq;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.AmqpAdmin;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 延迟消息支持：用"TTL 队列 + 死信交换机"实现延迟投递，无需安装任何 RabbitMQ 插件。
 * <p>
 * 实现方式：为"延迟档位 + 目标 Exchange + 目标 RoutingKey"声明一个专用队列，
 * 队列参数 x-message-ttl 为延迟毫秒数、x-dead-letter-exchange 为目标 Exchange；
 * 消息先投递到该队列滞留，到期后由 RabbitMQ 自动转入死信流程，按目标 RoutingKey
 * 重新投递到目标 Exchange，消费者拿到的消息与普通消息完全一致。
 * <p>
 * 档位沿用原 RocketMQ 开源版的 18 个固定档位（1s/5s/10s/30s/1m…2h），
 * 因此调用方传入的 delayLevel 语义与取值保持不变。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class RabbitMqDelaySupport {

    /** 延迟档位 → 毫秒，下标 0 对应档位 1 */
    private static final long[] LEVEL_MILLIS = {
            1_000L, 5_000L, 10_000L, 30_000L,
            60_000L, 120_000L, 180_000L, 240_000L, 300_000L,
            360_000L, 420_000L, 480_000L, 540_000L, 600_000L,
            1_200_000L, 1_800_000L, 3_600_000L, 7_200_000L
    };

    private final ObjectProvider<AmqpAdmin> amqpAdminProvider;

    /** 已声明过的延迟队列，避免每次发送都触发一次 declare RPC */
    private final Set<String> declaredQueues = ConcurrentHashMap.newKeySet();

    /**
     * 确保目标延迟队列存在并返回队列名；发布方随后向默认交换机投递该队列名即可。
     * AmqpAdmin 不可用（如关闭了 RabbitMQ 动态声明）时返回 null，由调用方按发送失败处理
     */
    public String ensureDelayQueue(String exchange, String routingKey, int delayLevel) {
        AmqpAdmin amqpAdmin = amqpAdminProvider.getIfAvailable();
        if (amqpAdmin == null) {
            log.warn("AmqpAdmin 不可用（spring.rabbitmq.dynamic=false），无法声明延迟队列：{}", exchange);
            return null;
        }
        int level = normalizeLevel(delayLevel);
        String queueName = MqQueues.delayQueue(exchange, routingKey, level);
        if (declaredQueues.contains(queueName)) {
            return queueName;
        }
        // 先确保目标 Exchange 存在，避免消费者尚未启动时报 "unknown exchange" 导致到期的消息无处可去
        amqpAdmin.declareExchange(new TopicExchange(exchange, true, false));
        amqpAdmin.declareQueue(QueueBuilder.durable(queueName)
                .ttl((int) LEVEL_MILLIS[level - 1])
                .deadLetterExchange(exchange)
                .deadLetterRoutingKey(routingKey)
                .build());
        declaredQueues.add(queueName);
        log.info("延迟队列已就绪：{}（延迟 {} ms，到期转入 exchange={}, routingKey={}）",
                queueName, LEVEL_MILLIS[level - 1], exchange, routingKey);
        return queueName;
    }

    /** 档位对应的延迟毫秒数 */
    public long delayMillis(int delayLevel) {
        return LEVEL_MILLIS[normalizeLevel(delayLevel) - 1];
    }

    /** 档位越界时收敛到合法区间，避免配置错误导致消息无法投递 */
    private int normalizeLevel(int delayLevel) {
        if (delayLevel < 1) {
            log.warn("延迟档位 {} 非法，按档位 1 处理", delayLevel);
            return 1;
        }
        if (delayLevel > LEVEL_MILLIS.length) {
            log.warn("延迟档位 {} 超出上限，按最大档位 {} 处理", delayLevel, LEVEL_MILLIS.length);
            return LEVEL_MILLIS.length;
        }
        return delayLevel;
    }
}
