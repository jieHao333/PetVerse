package com.my.petverse.common.mq;

/**
 * RabbitMQ 队列命名契约。
 * <p>
 * 队列名沿用原 RocketMQ 的 consumerGroup：RabbitMQ 中"一个队列 + 一个消费者"即对应
 * RocketMQ 中"一个消费组拿到一份消息"，因此队列名与消费组名保持一致，便于对照历史日志与监控。
 * 同时集中定义死信交换机/队列与延迟队列的命名规则。
 */
public final class MqQueues {

    private MqQueues() {
    }

    // ---------- 业务队列（名字 = 原 RocketMQ consumerGroup） ----------

    /** 宠物经验发放，消费 space-event-topic 的 created */
    public static final String PET_EXP = "pet-exp-consumer";

    /** 商户审批通过升级角色，消费 merchant-event-topic 的 approved */
    public static final String USER_ROLE = "user-role-consumer";

    /** 商户撤销冷静期结束降级角色，消费 merchant-event-topic 的 withdrawn */
    public static final String USER_ROLE_WITHDRAW = "user-role-withdraw-consumer";

    /** 商品检索索引维护，消费 product-index-topic 的四个 RoutingKey */
    public static final String PRODUCT_INDEX = "product-index-consumer";

    /** 订单超时取消，消费 order-timeout-topic 的 timeout（经延迟队列投递） */
    public static final String ORDER_TIMEOUT = "order-timeout-consumer";

    /** 站内通知落库，消费 notify-topic 的 created */
    public static final String REMARK_NOTIFICATION = "remark-notification-consumer";

    /** 动态检索索引维护，消费 space-event-topic 的 index-upsert / index-remove */
    public static final String SPACE_INDEX = "space-index-consumer";

    /** 点赞转通知，消费 like-changed-topic 的 changed */
    public static final String SPACE_LIKE_NOTIFY = "space-like-notify-consumer";

    /** 点赞数同步，消费 like-changed-topic 的 changed */
    public static final String SPACE_LIKE_COUNT = "space-like-count-consumer";

    /** 领券/兑换落库，消费 promotion-event-topic 的 receive */
    public static final String COUPON_RECEIVE = "coupon-receive-consumer";

    /** 数据中台采集，消费 data-event-topic 的 behavior，并复用各业务 Exchange 的事件落地 ODS */
    public static final String DATA_EVENT = "data-event-consumer";

    // ---------- 死信（重试耗尽后的兜底，对应原 RocketMQ 的 %DLQ%<consumerGroup>） ----------

    /** 死信交换机：fanout，所有重试耗尽的消息统一转入，避免消息静默丢失 */
    public static final String DEAD_LETTER_EXCHANGE = "petverse.dlx";

    /** 死信队列：保留原始消息体与 x-death 头（含原交换机/RoutingKey/失败原因），供人工排查与重放 */
    public static final String DEAD_LETTER_QUEUE = "petverse.dlq";

    /** 队列参数：死信交换机 */
    public static final String ARG_DEAD_LETTER_EXCHANGE = "x-dead-letter-exchange";

    /** 队列参数：死信 RoutingKey */
    public static final String ARG_DEAD_LETTER_ROUTING_KEY = "x-dead-letter-routing-key";

    // ---------- 延迟消息 ----------

    /** 延迟队列名前缀，完整名称见 {@link #delayQueue(String, String, int)} */
    public static final String DELAY_QUEUE_PREFIX = "petverse.delay.";

    /**
     * 延迟队列名：TTL 是队列级参数，因此"延迟档位 + 目标交换机 + 目标 RoutingKey"共同决定一个队列。
     * 队列内消息到期后经死信交换机投递到目标 Exchange，实现与原 RocketMQ 延迟消息一致的效果。
     */
    public static String delayQueue(String exchange, String routingKey, int delayLevel) {
        return DELAY_QUEUE_PREFIX + exchange + "." + routingKey + ".l" + delayLevel;
    }
}
