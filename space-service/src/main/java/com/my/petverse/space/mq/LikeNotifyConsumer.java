package com.my.petverse.space.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.enums.LikeTargetType;
import com.my.petverse.common.enums.NotificationSource;
import com.my.petverse.common.enums.NotificationType;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.LikeChangedMessage;
import com.my.petverse.common.mq.message.NotifyMessage;
import com.my.petverse.space.mapper.SpaceMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.util.Objects;

/**
 * 点赞通知消费者：新增点赞且对象为动态时，本地解析动态作者作为收件人，
 * 转发为通用通知事件（remark-service 统一落库）。仅在本服务能拿到作者，故由这里发出
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.LIKE_CHANGED,
        selectorExpression = MqTopics.TAG_LIKE_CHANGED,
        consumerGroup = "space-like-notify-consumer")
public class LikeNotifyConsumer implements RocketMQListener<MessageExt> {

    private final SpaceMapper spaceMapper;

    private final MqEventPublisher mqEventPublisher;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        LikeChangedMessage msg;
        try {
            msg = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    LikeChangedMessage.class);
        } catch (Exception e) {
            log.error("解析点赞事件消息失败，msgId={}", messageExt.getMsgId(), e);
            return;
        }
        // 仅处理"新增点赞"且对象为动态的场景，取消点赞与其他类型直接忽略
        if (!Boolean.TRUE.equals(msg.getAdded())
                || msg.getTargetType() == null
                || !msg.getTargetType().equals(LikeTargetType.SPACE.getCode())
                || msg.getTargetId() == null || msg.getActorUserId() == null) {
            return;
        }
        Space space = spaceMapper.selectById(msg.getTargetId());
        if (space == null || space.getUserId() == null) {
            return;
        }
        // 自己点赞自己的动态不通知
        if (Objects.equals(space.getUserId(), msg.getActorUserId())) {
            return;
        }
        NotifyMessage notify = new NotifyMessage(space.getUserId(), msg.getActorUserId(),
                NotificationType.LIKE.getCode(), NotificationSource.SPACE.getCode(),
                space.getId(), null);
        // 去重键：同一人对同一动态的一次点赞只通知一次
        mqEventPublisher.publish(MqTopics.NOTIFY, MqTopics.TAG_NOTIFY_CREATED,
                notify, "like:" + space.getId() + ":" + msg.getActorUserId());
    }
}
