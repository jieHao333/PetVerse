package com.my.petverse.common.vo.data;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDate;

/**
 * 按日指标出参。
 */
@Data
public class MetricDailyVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 统计日期 */
    private LocalDate statDate;
    /** 指标键 */
    private String metricKey;
    /** 指标值 */
    private Double metricValue;
    /** 维度键 */
    private String dimKey;
}