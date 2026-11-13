package com.my.petverse.common.vo.data;

import lombok.Data;

import java.io.Serializable;
import java.util.List;

/**
 * 管理端 BI 概览出参：资产总量 + 当日指标 + 热门商品。
 */
@Data
public class BiOverviewVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** ODS 原始事件总量 */
    private Long eventCount;
    /** DWD 行为明细总量 */
    private Long behaviorCount;
    /** 用户画像数量 */
    private Long profileCount;
    /** 商品统计数量 */
    private Long productCount;
    /** 反馈样本数量 */
    private Long sampleCount;
    /** 当日指标列表 */
    private List<MetricDailyVO> todayMetrics;
    /** 热门商品（按热度分降序，取前若干） */
    private List<ProductStatsVO> topProducts;
}