package com.my.petverse.promotion.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.UserCouponMessage;
import com.my.petverse.promotion.service.UserCouponService;
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
 * 领券/兑换消息消费者：领券 Lua 校验通过后发出的消息在此异步落库
 * （发放数条件自增防超发 + 写入 user_coupon + 更新兑换码状态），
 * 失败抛异常触发容器重试；重复投递由 incrIssueNum 条件更新与幂等状态机兜底
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class CouponReceiveConsumer {

    private final UserCouponService userCouponService;

    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = @QueueBinding(
                    value = @Queue(value = MqQueues.COUPON_RECEIVE, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE,
                                            value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY,
                                            value = MqQueues.COUPON_RECEIVE)}),
                    exchange = @Exchange(value = MqTopics.PROMOTION_EVENT,
                            type = ExchangeTypes.TOPIC, durable = "true"),
                    key = MqTopics.TAG_COUPON_RECEIVE),
            containerFactory = RabbitMqConfig.LISTENER_FACTORY)
    public void onMessage(Message message) {
        UserCouponMessage msg;
        try {
            msg = objectMapper.readValue(MqMessages.body(message), UserCouponMessage.class);
        } catch (Exception e) {
            log.error("解析领券消息失败，msgId={}", MqMessages.messageId(message), e);
            return;
        }
        if (msg.getUserId() == null || msg.getCouponId() == null) {
            return;
        }
        try {
            userCouponService.checkAndCreateUserCoupon(msg);
        } catch (Exception e) {
            log.error("处理领券消息失败，userId={}, couponId={}", msg.getUserId(), msg.getCouponId(), e);
            throw e;
        }
    }
}
