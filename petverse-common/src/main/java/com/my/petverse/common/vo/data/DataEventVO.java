package com.my.petverse.common.vo.data;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 原始事件出参（管理端 BI 排查与治理用）。
 */
@Data
public class DataEventVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 主键 */
    private Long id;
    /** 事件唯一ID */
    private String eventId;
    /** 来源 behavior/business */
    private String source;
    /** 事件类型 */
    private String eventType;
    /** 用户ID */
    private Long userId;
    /** 宠物ID */
    private Long petId;
    /** 目标类型 */
    private String targetType;
    /** 目标ID */
    private Long targetId;
    /** 场景 */
    private String scene;
    /** 原始载荷(JSON) */
    private String payload;
    /** 事件发生时间 */
    private LocalDateTime eventTime;
    /** 入库时间 */
    private LocalDateTime createTime;
}