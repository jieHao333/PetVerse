package com.my.petverse.data.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.entity.data.DataMetricDaily;
import com.my.petverse.common.vo.data.BiOverviewVO;
import com.my.petverse.common.vo.data.MetricDailyVO;

import java.time.LocalDate;
import java.util.List;

/**
 * 指标服务：按日统一口径指标聚合与管理端 BI 概览/趋势读取。
 */
public interface DataMetricService extends IService<DataMetricDaily> {

    /** 聚合指定日期的指标（DAU/事件量/推荐CTR/AI采纳率等） */
    void aggregate(LocalDate date);

    /** BI 概览：资产总量 + 当日指标 + 热门商品 */
    BiOverviewVO overview();

    /** 按日期区间读取指标趋势 */
    List<MetricDailyVO> listMetrics(LocalDate start, LocalDate end);
}