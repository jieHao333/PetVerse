package com.my.petverse.data.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.mq.MqDeduplicator;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.DataEventMessage;
import com.my.petverse.data.service.DataCollectService;
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
 * 数据中台采集消费者。
 * 一个队列绑定多个 Exchange：既接收前端埋点（data-event-topic/behavior），
 * 又复用现有业务领域事件（动态/点赞/订单超时/商户/领券），无需改动任何生产者即可把业务数据汇入 ODS。
 * 按消息键幂等去重，落地失败释放键交由容器重试，耗尽转死信。
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class DataEventConsumer {

    private final DataCollectService collectService;
    private final MqDeduplicator mqDeduplicator;
    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = {
                    @QueueBinding(value = @Queue(value = MqQueues.DATA_EVENT, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE, value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY, value = MqQueues.DATA_EVENT)}),
                            exchange = @Exchange(value = MqTopics.DATA_EVENT, type = ExchangeTypes.TOPIC, durable = "true"),
                            key = MqTopics.TAG_DATA_BEHAVIOR),
                    @QueueBinding(value = @Queue(value = MqQueues.DATA_EVENT, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE, value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY, value = MqQueues.DATA_EVENT)}),
                            exchange = @Exchange(value = MqTopics.SPACE_EVENT, type = ExchangeTypes.TOPIC, durable = "true"),
                            key = MqTopics.TAG_SPACE_CREATED),
                    @QueueBinding(value = @Queue(value = MqQueues.DATA_EVENT, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE, value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY, value = MqQueues.DATA_EVENT)}),
                            exchange = @Exchange(value = MqTopics.LIKE_CHANGED, type = ExchangeTypes.TOPIC, durable = "true"),
                            key = MqTopics.TAG_LIKE_CHANGED),
                    @QueueBinding(value = @Queue(value = MqQueues.DATA_EVENT, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE, value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY, value = MqQueues.DATA_EVENT)}),
                            exchange = @Exchange(value = MqTopics.ORDER_TIMEOUT, type = ExchangeTypes.TOPIC, durable = "true"),
                            key = MqTopics.TAG_ORDER_TIMEOUT),
                    @QueueBinding(value = @Queue(value = MqQueues.DATA_EVENT, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE, value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY, value = MqQueues.DATA_EVENT)}),
                            exchange = @Exchange(value = MqTopics.MERCHANT_EVENT, type = ExchangeTypes.TOPIC, durable = "true"),
                            key = MqTopics.TAG_MERCHANT_APPROVED),
                    @QueueBinding(value = @Queue(value = MqQueues.DATA_EVENT, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE, value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY, value = MqQueues.DATA_EVENT)}),
                            exchange = @Exchange(value = MqTopics.PROMOTION_EVENT, type = ExchangeTypes.TOPIC, durable = "true"),
                            key = MqTopics.TAG_COUPON_RECEIVE)
            },
            containerFactory = RabbitMqConfig.LISTENER_FACTORY)
    public void onMessage(Message message) {
        String exchange = message.getMessageProperties().getReceivedExchange();
        String routingKey = message.getMessageProperties().getReceivedRoutingKey();
        String body = MqMessages.body(message);
        String key = MqMessages.key(message);

        if (MqTopics.DATA_EVENT.equals(exchange)) {
            DataEventMessage msg;
            try {
                msg = objectMapper.readValue(body, DataEventMessage.class);
            } catch (Exception e) {
                log.error("解析行为事件失败，msgId={}", MqMessages.messageId(message), e);
                return;
            }
            String dedupKey = "data-behavior:" + msg.getEventId();
            if (!mqDeduplicator.firstTime(dedupKey)) {
                return;
            }
            try {
                collectService.persistEvent(msg);
            } catch (Exception e) {
                mqDeduplicator.release(dedupKey);
                log.error("行为事件落地失败，key={}", dedupKey, e);
                throw e;
            }
        } else {
            String dedupKey = "data-biz:" + exchange + ":" + routingKey + ":" + key;
            if (!mqDeduplicator.firstTime(dedupKey)) {
                return;
            }
            try {
                collectService.persistBusiness(exchange, routingKey, body, key);
            } catch (Exception e) {
                mqDeduplicator.release(dedupKey);
                log.error("业务事件落地失败，key={}", dedupKey, e);
                throw e;
            }
        }
    }
}