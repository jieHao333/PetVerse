package com.my.petverse.remark.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.mq.MqDeduplicator;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.NotifyMessage;
import com.my.petverse.remark.service.NotificationService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;

/**
 * 站内通知事件消费者：统一接收评论回复/评价回复/点赞三类通知事件并落库，
 * RocketMQ 至少投递一次，按消息 key（源实体ID）幂等去重，落库失败释放键交由重试
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.NOTIFY,
        selectorExpression = MqTopics.TAG_NOTIFY_CREATED,
        consumerGroup = "remark-notification-consumer")
public class NotificationConsumer implements RocketMQListener<MessageExt> {

    private final NotificationService notificationService;

    private final MqDeduplicator mqDeduplicator;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        NotifyMessage message;
        try {
            message = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    NotifyMessage.class);
        } catch (Exception e) {
            log.error("解析通知事件消息失败，msgId={}", messageExt.getMsgId(), e);
            return;
        }
        if (message.getRecipientUserId() == null) {
            return;
        }
        String key = messageExt.getKeys();
        // 首次出现才落库，重复投递直接跳过
        if (!mqDeduplicator.firstTime(key)) {
            return;
        }
        try {
            notificationService.saveFromMessage(message);
        } catch (Exception e) {
            // 落库失败释放去重键，依赖 RocketMQ 重试重新消费
            mqDeduplicator.release(key);
            log.error("通知落库失败，key={}", key, e);
            throw e;
        }
    }
}
