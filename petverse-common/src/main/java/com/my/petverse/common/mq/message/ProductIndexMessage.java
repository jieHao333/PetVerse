package com.my.petverse.common.mq.message;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 商品索引事件消息：消费方为 shop-service，异步维护商品 Elasticsearch 检索索引，
 * 具体动作由消息 Tag 区分（upsert / remove / shop-renamed）
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class ProductIndexMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品ID（shop-renamed 事件不使用） */
    private Long productId;

    /** 店铺ID（shop-renamed 事件使用） */
    private Long merchantId;
}
