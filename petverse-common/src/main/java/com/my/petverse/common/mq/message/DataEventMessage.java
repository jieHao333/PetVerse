package com.my.petverse.common.mq.message;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 数据中台行为事件消息体（data-event-topic / behavior）。
 * 由埋点接入端发布，采集消费者据此落地 ODS 并清洗到 DWD。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class DataEventMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 事件唯一ID（幂等键） */
    private String eventId;
    /** 事件来源：behavior */
    private String source;
    /** 事件类型 */
    private String eventType;
    /** 用户ID */
    private Long userId;
    /** 宠物ID */
    private Long petId;
    /** 目标对象类型 */
    private String targetType;
    /** 目标对象ID */
    private Long targetId;
    /** 场景 */
    private String scene;
    /** 位置 */
    private Integer position;
    /** 搜索关键词 */
    private String keyword;
    /** 物种 */
    private String species;
    /** AI反馈：1/-1/0 */
    private Integer feedback;
    /** 原始载荷（JSON 文本） */
    private String payload;
    /** 事件时间（ISO-8601 文本） */
    private String eventTime;
}