package com.my.petverse.shop.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.OrderTimeoutMessage;
import com.my.petverse.shop.service.OrderService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;

/**
 * 订单超时消费者：消费下单时发出的延迟消息，到期后若订单仍为待支付则自动取消并回补库存。
 * 消费逻辑按订单状态判断，天然幂等：消息重复投递、订单已支付/已取消时均安全跳过
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.ORDER_TIMEOUT,
        selectorExpression = MqTopics.TAG_ORDER_TIMEOUT,
        consumerGroup = "order-timeout-consumer")
public class OrderTimeoutConsumer implements RocketMQListener<MessageExt> {

    private final OrderService orderService;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        OrderTimeoutMessage msg;
        try {
            msg = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    OrderTimeoutMessage.class);
        } catch (Exception e) {
            log.error("解析订单超时消息失败，msgId={}", messageExt.getMsgId(), e);
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
