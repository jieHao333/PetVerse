package com.my.petverse.common.entity.data;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * 数据中台 ODS 原始事件层，对应表 data_event_raw。
 * 行为埋点与业务领域事件统一以原始 JSON 落地，保留现场供清洗与回溯，按 eventId 幂等。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("data_event_raw")
public class DataEventRaw extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 事件唯一ID（幂等键：行为事件由前端生成，业务事件由交换机+业务键派生） */
    private String eventId;
    /** 事件来源：behavior-行为埋点 business-业务领域事件 */
    private String source;
    /** 事件类型：exposure/click/search/ai_feedback/page_view/space_created/like_changed 等 */
    private String eventType;
    /** 用户ID（可空，匿名行为为空） */
    private Long userId;
    /** 宠物ID（可空） */
    private Long petId;
    /** 目标对象类型：product/space/ai_message/page/coupon 等 */
    private String targetType;
    /** 目标对象ID */
    private Long targetId;
    /** 场景：home/shop/pet_chat 等 */
    private String scene;
    /** 原始事件载荷（JSON 文本） */
    private String payload;
    /** 事件发生时间 */
    private LocalDateTime eventTime;
}