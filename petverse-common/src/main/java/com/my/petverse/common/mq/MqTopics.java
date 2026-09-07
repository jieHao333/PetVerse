package com.my.petverse.common.mq;

/**
 * RocketMQ Topic 与 Tag 契约，生产者与消费者统一引用此处常量，避免硬编码不一致。
 * 注意：各服务需配置 rocketmq.name-server，开源版延迟消息仅支持固定档位
 *（14 = 10分钟，15 = 20分钟）
 */
public final class MqTopics {

    private MqTopics() {
    }

    /** 动态事件（发布奖励宠物经验、检索索引维护） */
    public static final String SPACE_EVENT = "space-event-topic";

    /** 动态发布：消费方为 pet-service，发放宠物经验 */
    public static final String TAG_SPACE_CREATED = "created";

    /** 动态索引新增/更新：消费方为 space-service 自身 */
    public static final String TAG_SPACE_INDEX_UPSERT = "index-upsert";

    /** 动态索引删除：消费方为 space-service 自身 */
    public static final String TAG_SPACE_INDEX_REMOVE = "index-remove";

    /** 商品检索索引事件（新增/更新、删除、店铺改名重刷） */
    public static final String PRODUCT_INDEX = "product-index-topic";

    public static final String TAG_PRODUCT_UPSERT = "upsert";

    public static final String TAG_PRODUCT_REMOVE = "remove";

    public static final String TAG_PRODUCT_SHOP_RENAMED = "shop-renamed";

    /** 订单超时自动取消（延迟消息） */
    public static final String ORDER_TIMEOUT = "order-timeout-topic";

    public static final String TAG_ORDER_TIMEOUT = "timeout";

    /** 商户事件（入驻审批通过后升级用户角色） */
    public static final String MERCHANT_EVENT = "merchant-event-topic";

    public static final String TAG_MERCHANT_APPROVED = "approved";

    /** 点赞变更事件（驱动业务侧热度冗余列近实时同步） */
    public static final String LIKE_CHANGED = "like-changed-topic";

    public static final String TAG_LIKE_CHANGED = "changed";

    /** 站内通知事件（评论回复/评价回复/点赞统一驱动通知落库） */
    public static final String NOTIFY = "notify-topic";

    public static final String TAG_NOTIFY_CREATED = "created";
}
