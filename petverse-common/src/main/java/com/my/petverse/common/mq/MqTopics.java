package com.my.petverse.common.mq;

/**
 * RabbitMQ Exchange 与 RoutingKey 契约，生产者与消费者统一引用此处常量，避免硬编码不一致。
 * <p>
 * 映射规则：原 RocketMQ 的 topic 作为 RabbitMQ 的 topic 类型 Exchange 名称，
 * 原 RocketMQ 的 tag 作为 RoutingKey（消费端每个队列按需绑定一个或多个 RoutingKey，
 * 等价于原 selectorExpression 的 "tagA || tagB"）。
 * 各服务需配置 spring.rabbitmq.*，延迟消息由 TTL + 死信队列实现，支持任意档位。
 */
public final class MqTopics {

    private MqTopics() {
    }

    /** 动态事件 Exchange（发布奖励宠物经验、检索索引维护） */
    public static final String SPACE_EVENT = "space-event-topic";

    /** 动态发布：消费方为 pet-service，发放宠物经验 */
    public static final String TAG_SPACE_CREATED = "created";

    /** 动态索引新增/更新：消费方为 space-service 自身 */
    public static final String TAG_SPACE_INDEX_UPSERT = "index-upsert";

    /** 动态索引删除：消费方为 space-service 自身 */
    public static final String TAG_SPACE_INDEX_REMOVE = "index-remove";

    /** 商品检索索引 Exchange（新增/更新、删除、店铺改名/营业状态变更重刷） */
    public static final String PRODUCT_INDEX = "product-index-topic";

    public static final String TAG_PRODUCT_UPSERT = "upsert";

    public static final String TAG_PRODUCT_REMOVE = "remove";

    public static final String TAG_PRODUCT_SHOP_RENAMED = "shop-renamed";

    public static final String TAG_PRODUCT_SHOP_CHANGED = "shop-changed";

    /** 订单超时自动取消 Exchange（延迟消息） */
    public static final String ORDER_TIMEOUT = "order-timeout-topic";

    public static final String TAG_ORDER_TIMEOUT = "timeout";

    /** 商户事件 Exchange（入驻审批通过后升级用户角色、撤销冷静期结束后降级用户角色） */
    public static final String MERCHANT_EVENT = "merchant-event-topic";

    public static final String TAG_MERCHANT_APPROVED = "approved";

    public static final String TAG_MERCHANT_WITHDRAWN = "withdrawn";

    /** 点赞变更事件 Exchange（驱动业务侧热度冗余列近实时同步） */
    public static final String LIKE_CHANGED = "like-changed-topic";

    public static final String TAG_LIKE_CHANGED = "changed";

    /** 站内通知事件 Exchange（评论回复/评价回复/点赞统一驱动通知落库） */
    public static final String NOTIFY = "notify-topic";

    public static final String TAG_NOTIFY_CREATED = "created";

    /** 促销事件 Exchange（优惠券领取/兑换码兑换成功后异步落库） */
    public static final String PROMOTION_EVENT = "promotion-event-topic";

    /** 领券/兑换：消费方为 promotion-service 自身，完成发放数自增与 user_coupon 落库 */
    public static final String TAG_COUPON_RECEIVE = "receive";

    /** 数据中台事件 Exchange（前端埋点行为事件统一入口） */
    public static final String DATA_EVENT = "data-event-topic";

    /** 行为埋点：曝光/点击/搜索/AI反馈/页面浏览，消费方为 data-service */
    public static final String TAG_DATA_BEHAVIOR = "behavior";
}
