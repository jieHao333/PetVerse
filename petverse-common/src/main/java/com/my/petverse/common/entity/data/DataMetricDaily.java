package com.my.petverse.common.entity.data;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDate;

/**
 * 数据中台 ADS 指标层，对应表 data_metric_daily。
 * 统一口径的按日指标（DAU/事件量/推荐CTR/AI采纳率等），供管理端 BI 看板消费。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("data_metric_daily")
public class DataMetricDaily extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 统计日期 */
    private LocalDate statDate;
    /** 指标键：dau/event_count/recommend_exposure/recommend_click/recommend_ctr/ai_adoption_rate 等 */
    private String metricKey;
    /** 指标值 */
    private Double metricValue;
    /** 维度键（可选，默认空串；用于按场景/物种等拆分） */
    private String dimKey;
}