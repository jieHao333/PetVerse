package com.my.petverse.promotion.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.UserCouponMessage;
import com.my.petverse.promotion.service.UserCouponService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;

/**
 * 领券/兑换消息消费者：领券 Lua 校验通过后发出的消息在此异步落库
 * （发放数条件自增防超发 + 写入 user_coupon + 更新兑换码状态），
 * 失败抛异常触发 Broker 重试；重复投递由 incrIssueNum 条件更新与幂等状态机兜底
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.PROMOTION_EVENT,
        selectorExpression = MqTopics.TAG_COUPON_RECEIVE,
        consumerGroup = "coupon-receive-consumer")
public class CouponReceiveConsumer implements RocketMQListener<MessageExt> {

    private final UserCouponService userCouponService;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        UserCouponMessage msg;
        try {
            msg = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    UserCouponMessage.class);
        } catch (Exception e) {
            log.error("解析领券消息失败，msgId={}", messageExt.getMsgId(), e);
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
