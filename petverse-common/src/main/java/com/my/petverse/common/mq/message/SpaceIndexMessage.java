package com.my.petverse.common.mq.message;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 动态索引事件消息：消费方为 space-service，异步维护 Elasticsearch 检索索引，
 * 具体动作由消息 Tag 区分（index-upsert / index-remove）
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class SpaceIndexMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 动态ID */
    private Long spaceId;
}
