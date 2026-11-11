package com.my.petverse.shop.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.OrderTimeoutMessage;
import com.my.petverse.shop.service.OrderService;
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
 * 订单超时消费者：消费下单时发出的延迟消息，到期后若订单仍为待支付则自动取消并回补库存。
 * 消息先进入 TTL 队列滞留，到期后经死信交换机回到 order-timeout-topic，
 * 因此这里与普通消息的消费方式完全一致。
 * 消费逻辑按订单状态判断，天然幂等：消息重复投递、订单已支付/已取消时均安全跳过
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class OrderTimeoutConsumer {

    private final OrderService orderService;

    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = @QueueBinding(
                    value = @Queue(value = MqQueues.ORDER_TIMEOUT, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE,
                                            value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY,
                                            value = MqQueues.ORDER_TIMEOUT)}),
                    exchange = @Exchange(value = MqTopics.ORDER_TIMEOUT,
                            type = ExchangeTypes.TOPIC, durable = "true"),
                    key = MqTopics.TAG_ORDER_TIMEOUT),
            containerFactory = RabbitMqConfig.LISTENER_FACTORY)
    public void onMessage(Message message) {
        OrderTimeoutMessage msg;
        try {
            msg = objectMapper.readValue(MqMessages.body(message), OrderTimeoutMessage.class);
        } catch (Exception e) {
            log.error("解析订单超时消息失败，msgId={}", MqMessages.messageId(message), e);
            return;
        }
        if (msg.getOrderId() == null) {
            return;
        }
        try {
            boolean cancelled = orderService.timeoutCancelOrder(msg.getOrderId());
            if (cancelled) {
                log.info("订单超时未支付，已自动取消并回补库存，orderId={}", msg.getOrderId());
            }
        } catch (Exception e) {
            log.error("处理订单超时取消失败，orderId={}", msg.getOrderId(), e);
            throw e;
        }
    }
}
