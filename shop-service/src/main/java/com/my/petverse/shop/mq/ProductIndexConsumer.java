package com.my.petverse.shop.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.mq.MqMessages;
import com.my.petverse.common.mq.MqQueues;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.RabbitMqConfig;
import com.my.petverse.common.mq.message.ProductIndexMessage;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import com.my.petverse.shop.search.ProductSearchService;
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
 * 商品检索索引消费者：异步维护 Elasticsearch 商品索引，
 * 替代商品增删改与店铺改名路径上的同步双写；
 * 索引操作按ID回表读取最新数据，天然幂等，失败由监听容器重试
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "petverse.mq", name = "enabled", havingValue = "true", matchIfMissing = true)
public class ProductIndexConsumer {

    private final ProductMapper productMapper;

    private final MerchantMapper merchantMapper;

    private final ProductSearchService productSearchService;

    private final ObjectMapper objectMapper;

    @RabbitListener(
            bindings = @QueueBinding(
                    value = @Queue(value = MqQueues.PRODUCT_INDEX, durable = "true",
                            arguments = {
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_EXCHANGE,
                                            value = MqQueues.DEAD_LETTER_EXCHANGE),
                                    @Argument(name = MqQueues.ARG_DEAD_LETTER_ROUTING_KEY,
                                            value = MqQueues.PRODUCT_INDEX)}),
                    exchange = @Exchange(value = MqTopics.PRODUCT_INDEX,
                            type = ExchangeTypes.TOPIC, durable = "true"),
                    // 等价于原 selectorExpression 的四个 tag 并联
                    key = {MqTopics.TAG_PRODUCT_UPSERT, MqTopics.TAG_PRODUCT_REMOVE,
                            MqTopics.TAG_PRODUCT_SHOP_RENAMED, MqTopics.TAG_PRODUCT_SHOP_CHANGED}),
            containerFactory = RabbitMqConfig.LISTENER_FACTORY)
    public void onMessage(Message message) {
        ProductIndexMessage msg;
        try {
            msg = objectMapper.readValue(MqMessages.body(message), ProductIndexMessage.class);
        } catch (Exception e) {
            log.error("解析商品索引事件消息失败，msgId={}", MqMessages.messageId(message), e);
            return;
        }
        String tag = MqMessages.tag(message);
        try {
            if (MqTopics.TAG_PRODUCT_REMOVE.equals(tag)) {
                // 重复删除幂等，无需额外去重
                productSearchService.removeProduct(msg.getProductId());
            } else if (MqTopics.TAG_PRODUCT_SHOP_RENAMED.equals(tag)
                    || MqTopics.TAG_PRODUCT_SHOP_CHANGED.equals(tag)) {
                refreshMerchantProducts(msg.getMerchantId());
            } else {
                indexProduct(msg.getProductId());
            }
        } catch (Exception e) {
            log.error("维护商品检索索引失败，tag={}, productId={}", tag, msg.getProductId(), e);
            throw e;
        }
    }

    /** upsert：回表读取商品与店铺名刷索引，商品已删除时忽略 */
    private void indexProduct(Long productId) {
        if (productId == null) {
            return;
        }
        Product product = productMapper.selectById(productId);
        if (product == null) {
            log.warn("索引事件对应的商品不存在（可能已删除），productId={}", productId);
            return;
        }
        Merchant merchant = merchantMapper.selectById(product.getMerchantId());
        String shopName = merchant == null ? "" : merchant.getShopName();
        productSearchService.indexProduct(product, shopName, merchant != null && merchant.isSelling());
    }

    /** 店铺改名/营业状态变更：按最新店铺信息重刷该店全部商品索引 */
    private void refreshMerchantProducts(Long merchantId) {
        if (merchantId == null) {
            return;
        }
        Merchant merchant = merchantMapper.selectById(merchantId);
        if (merchant == null) {
            log.warn("店铺索引事件对应的店铺不存在，merchantId={}", merchantId);
            return;
        }
        productSearchService.refreshMerchantProducts(merchantId);
    }
}
