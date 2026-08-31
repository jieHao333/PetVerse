package com.my.petverse.shop.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.ProductIndexMessage;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import com.my.petverse.shop.search.ProductSearchService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;

/**
 * 商品检索索引消费者：异步维护 Elasticsearch 商品索引，
 * 替代商品增删改与店铺改名路径上的同步双写；
 * 索引操作按ID回表读取最新数据，天然幂等，失败由 RocketMQ 重试
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(topic = MqTopics.PRODUCT_INDEX,
        selectorExpression = MqTopics.TAG_PRODUCT_UPSERT + " || " + MqTopics.TAG_PRODUCT_REMOVE
                + " || " + MqTopics.TAG_PRODUCT_SHOP_RENAMED,
        consumerGroup = "product-index-consumer")
public class ProductIndexConsumer implements RocketMQListener<MessageExt> {

    private final ProductMapper productMapper;

    private final MerchantMapper merchantMapper;

    private final ProductSearchService productSearchService;

    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(MessageExt messageExt) {
        ProductIndexMessage msg;
        try {
            msg = objectMapper.readValue(new String(messageExt.getBody(), StandardCharsets.UTF_8),
                    ProductIndexMessage.class);
        } catch (Exception e) {
            log.error("解析商品索引事件消息失败，msgId={}", messageExt.getMsgId(), e);
            return;
        }
        String tag = messageExt.getTags();
        try {
            if (MqTopics.TAG_PRODUCT_REMOVE.equals(tag)) {
                // 重复删除幂等，无需额外去重
                productSearchService.removeProduct(msg.getProductId());
            } else if (MqTopics.TAG_PRODUCT_SHOP_RENAMED.equals(tag)) {
                refreshShopName(msg.getMerchantId());
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
        productSearchService.indexProduct(product, shopName);
    }

    /** 店铺改名：按最新店铺名重刷该店全部商品索引 */
    private void refreshShopName(Long merchantId) {
        if (merchantId == null) {
            return;
        }
        Merchant merchant = merchantMapper.selectById(merchantId);
        if (merchant == null) {
            log.warn("改名事件对应的店铺不存在，merchantId={}", merchantId);
            return;
        }
        productSearchService.refreshShopName(merchantId, merchant.getShopName());
    }
}
