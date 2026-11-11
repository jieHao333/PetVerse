package com.my.petverse.remark.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.mq.MqDeduplicator;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.NotifyMessage;
import com.my.petverse.remark.service.NotificationService;
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
 * 站内通知事件消费者：统一接收评论回复/评价回复/点赞三类通知事件并落库，
 * 至少投递一次语义下按消息 key（源实体ID）幂等去重，落库失败释放键交由容器重试；
 * 重试次数与原 maxReconsumeTimes = 5 保持一致，耗尽后转入死信队列
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class NotificationConsumer {

    private final NotificationService notificationService;

    private final MqDeduplicator mqDeduplicator;

    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = @QueueBinding(
                    value = @Queue(value = MqQueues.REMARK_NOTIFICATION, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE,
                                            value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY,
                                            value = MqQueues.REMARK_NOTIFICATION)}),
                    exchange = @Exchange(value = MqTopics.NOTIFY,
                            type = ExchangeTypes.TOPIC, durable = "true"),
                    key = MqTopics.TAG_NOTIFY_CREATED),
            containerFactory = RabbitMqConfig.SHORT_RETRY_LISTENER_FACTORY)
    public void onMessage(Message message) {
        NotifyMessage msg;
        try {
            msg = objectMapper.readValue(MqMessages.body(message), NotifyMessage.class);
        } catch (Exception e) {
            log.error("解析通知事件消息失败，msgId={}", MqMessages.messageId(message), e);
            return;
        }
        if (msg.getRecipientUserId() == null) {
            return;
        }
        String key = MqMessages.key(message);
        // 首次出现才落库，重复投递直接跳过
        if (!mqDeduplicator.firstTime(key)) {
            return;
        }
        try {
            notificationService.saveFromMessage(msg);
        } catch (Exception e) {
            // 落库失败释放去重键，依赖容器重试重新消费
            mqDeduplicator.release(key);
            log.error("通知落库失败，key={}", key, e);
            throw e;
        }
    }
}
