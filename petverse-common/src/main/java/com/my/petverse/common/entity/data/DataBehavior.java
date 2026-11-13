package com.my.petverse.common.entity.data;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * 数据中台 DWD 行为明细层，对应表 data_behavior。
 * 由 ODS 行为事件清洗标准化而来，是画像、商品统计、指标与反馈样本的统一事实来源。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("data_behavior")
public class DataBehavior extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 事件唯一ID（幂等键，与 ODS 一致） */
    private String eventId;
    /** 用户ID */
    private Long userId;
    /** 事件类型：exposure-曝光 click-点击 search-搜索 ai_feedback-AI反馈 page_view-页面浏览 */
    private String eventType;
    /** 目标对象类型：product/space/ai_message/page */
    private String targetType;
    /** 目标对象ID */
    private Long targetId;
    /** 场景：home/shop/pet_chat 等 */
    private String scene;
    /** 曝光/点击位置（推荐流序号，从0开始） */
    private Integer position;
    /** 搜索关键词 */
    private String keyword;
    /** 关联物种（用于物种亲和度画像） */
    private String species;
    /** AI反馈：1-有用 -1-无用 0-无 */
    private Integer feedback;
    /** 事件发生时间 */
    private LocalDateTime eventTime;
}