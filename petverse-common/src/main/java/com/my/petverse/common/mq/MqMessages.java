package com.my.petverse.common.mq;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.MessageProperties;

import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.UUID;

/**
 * 消息体与消息属性的统一编解码：屏蔽 RabbitMQ Message 细节，保证生产端与消费端对
 * "消息体编码、业务去重键、RoutingKey（原 tag）"的约定完全一致。
 * <p>
 * 与原 RocketMQ 实现的对应关系：
 * <ul>
 *   <li>消息体：JSON（UTF-8），与原 RocketMQTemplate 的 JSON 序列化结果一致；</li>
 *   <li>key（原 {@code RocketMQHeaders.KEYS}）：写入 messageId，消费端据此幂等去重；</li>
 *   <li>tag（原 {@code MessageExt#getTags()}）：即 RabbitMQ 的 RoutingKey。</li>
 * </ul>
 */
public final class MqMessages {

    /** 业务去重键（同时写入 messageId，便于在控制台按 messageId 检索） */
    public static final String HEADER_KEY = "mq-key";

    /** 事件 tag，便于在控制台与死信队列中直接识别事件类型 */
    public static final String HEADER_TAG = "mq-tag";

    public static final String CONTENT_TYPE_JSON = "application/json";

    private MqMessages() {
    }

    /**
     * 构造持久化 JSON 消息；序列化失败向上抛出，由发布器统一捕获并降级为发送失败
     */
    public static Message build(ObjectMapper objectMapper, Object payload, String key, String tag)
            throws JsonProcessingException {
        MessageProperties properties = new MessageProperties();
        properties.setContentType(CONTENT_TYPE_JSON);
        properties.setContentEncoding(StandardCharsets.UTF_8.name());
        // 持久化消息：与原 RocketMQ 同步发送的可靠性对齐
        properties.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
        // 无业务键时退化为唯一 messageId，保证每条消息仍可被追踪
        properties.setMessageId(key == null || key.isEmpty() ? UUID.randomUUID().toString() : key);
        if (key != null && !key.isEmpty()) {
            properties.setHeader(HEADER_KEY, key);
        }
        if (tag != null) {
            properties.setHeader(HEADER_TAG, tag);
        }
        properties.setTimestamp(new Date());
        return new Message(objectMapper.writeValueAsBytes(payload), properties);
    }

    /** 消息体按 UTF-8 还原为 JSON 字符串 */
    public static String body(Message message) {
        return new String(message.getBody(), StandardCharsets.UTF_8);
    }

    /**
     * 事件 tag：优先取实际投递的 RoutingKey（与原 MessageExt#getTags() 语义一致，
     * 延迟消息经死信重新投递后 RoutingKey 即目标 tag），缺失时回退到消息头
     */
    public static String tag(Message message) {
        String routingKey = message.getMessageProperties().getReceivedRoutingKey();
        if (routingKey != null && !routingKey.isEmpty()) {
            return routingKey;
        }
        return (String) message.getMessageProperties().getHeader(HEADER_TAG);
    }

    /**
     * 业务去重键：与原 MessageExt#getKeys() 语义一致；未携带业务键时返回 messageId，
     * 保证同一条消息重复投递时键值稳定
     */
    public static String key(Message message) {
        String key = (String) message.getMessageProperties().getHeader(HEADER_KEY);
        return key != null && !key.isEmpty() ? key : message.getMessageProperties().getMessageId();
    }

    /** 消息ID，仅用于日志定位 */
    public static String messageId(Message message) {
        return message.getMessageProperties().getMessageId();
    }
}
