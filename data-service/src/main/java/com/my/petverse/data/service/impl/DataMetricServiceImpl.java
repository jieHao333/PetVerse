package com.my.petverse.data.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.entity.data.DataEventRaw;
import com.my.petverse.common.entity.data.DataMetricDaily;
import com.my.petverse.common.vo.data.BiOverviewVO;
import com.my.petverse.common.vo.data.MetricDailyVO;
import com.my.petverse.data.mapper.DataBehaviorMapper;
import com.my.petverse.data.mapper.DataEventRawMapper;
import com.my.petverse.data.mapper.DataMetricDailyMapper;
import com.my.petverse.data.mapper.DataProductStatsMapper;
import com.my.petverse.data.mapper.DataSampleMapper;
import com.my.petverse.data.mapper.DataUserProfileMapper;
import com.my.petverse.data.service.DataAssetService;
import com.my.petverse.data.service.DataMetricService;
import com.my.petverse.data.support.AggValues;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 指标服务实现。指标统一在此定义口径，按 (日期, 指标键, 维度) 唯一 upsert，可重复聚合。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DataMetricServiceImpl extends ServiceImpl<DataMetricDailyMapper, DataMetricDaily>
        implements DataMetricService {

    private final DataBehaviorMapper behaviorMapper;
    private final DataEventRawMapper eventRawMapper;
    private final DataUserProfileMapper userProfileMapper;
    private final DataProductStatsMapper productStatsMapper;
    private final DataSampleMapper sampleMapper;
    private final DataAssetService assetService;

    @Override
    public void aggregate(LocalDate date) {
        Long dau = behaviorMapper.countActiveUsers(date);
        Map<String, Object> m = behaviorMapper.aggregateDailyMetrics(date);
        long recExposure = AggValues.getLong(m, "recExposure");
        long recClick = AggValues.getLong(m, "recClick");
        long aiUp = AggValues.getLong(m, "aiUp");
        long aiDown = AggValues.getLong(m, "aiDown");
        long searchCnt = AggValues.getLong(m, "searchCnt");
        long pageView = AggValues.getLong(m, "pageView");
        long behaviorCount = AggValues.getLong(m, "behaviorCount");
        long eventCount = countRawByDate(date);
        double recCtr = recExposure > 0 ? (double) recClick / recExposure : 0d;
        double adoption = (aiUp + aiDown) > 0 ? (double) aiUp / (aiUp + aiDown) : 0d;

        upsertMetric(date, "dau", dau == null ? 0 : dau);
        upsertMetric(date, "event_count", eventCount);
        upsertMetric(date, "behavior_count", behaviorCount);
        upsertMetric(date, "recommend_exposure", recExposure);
        upsertMetric(date, "recommend_click", recClick);
        upsertMetric(date, "recommend_ctr", round(recCtr, 4));
        upsertMetric(date, "ai_feedback_up", aiUp);
        upsertMetric(date, "ai_feedback_down", aiDown);
        upsertMetric(date, "ai_adoption_rate", round(adoption, 4));
        upsertMetric(date, "search_count", searchCnt);
        upsertMetric(date, "page_view", pageView);
    }

    @Override
    public BiOverviewVO overview() {
        BiOverviewVO vo = new BiOverviewVO();
        vo.setEventCount(eventRawMapper.selectCount(null));
        vo.setBehaviorCount(behaviorMapper.selectCount(null));
        vo.setProfileCount(userProfileMapper.selectCount(null));
        vo.setProductCount(productStatsMapper.selectCount(null));
        vo.setSampleCount(sampleMapper.selectCount(null));
        LocalDate today = LocalDate.now();
        vo.setTodayMetrics(listMetrics(today, today));
        BasePageQuery topQuery = new BasePageQuery();
        topQuery.setPageNum(1);
        topQuery.setPageSize(10);
        vo.setTopProducts(assetService.pageProductStats(topQuery).getRecords());
        return vo;
    }

    @Override
    public List<MetricDailyVO> listMetrics(LocalDate start, LocalDate end) {
        List<DataMetricDaily> list = baseMapper.selectList(new LambdaQueryWrapper<DataMetricDaily>()
                .ge(DataMetricDaily::getStatDate, start)
                .le(DataMetricDaily::getStatDate, end)
                .orderByAsc(DataMetricDaily::getStatDate)
                .orderByAsc(DataMetricDaily::getMetricKey));
        return list.stream().map(this::toVO).collect(Collectors.toList());
    }

    private long countRawByDate(LocalDate date) {
        Long count = eventRawMapper.selectCount(new LambdaQueryWrapper<DataEventRaw>()
                .ge(DataEventRaw::getEventTime, date.atStartOfDay())
                .lt(DataEventRaw::getEventTime, date.plusDays(1).atStartOfDay()));
        return count == null ? 0L : count;
    }

    private void upsertMetric(LocalDate date, String key, double value) {
        DataMetricDaily metric = baseMapper.selectOne(new LambdaQueryWrapper<DataMetricDaily>()
                .eq(DataMetricDaily::getStatDate, date)
                .eq(DataMetricDaily::getMetricKey, key)
                .eq(DataMetricDaily::getDimKey, ""));
        if (metric == null) {
            metric = new DataMetricDaily();
            metric.setStatDate(date);
            metric.setMetricKey(key);
            metric.setDimKey("");
        }
        metric.setMetricValue(value);
        if (metric.getId() == null) {
            save(metric);
        } else {
            updateById(metric);
        }
    }

    private MetricDailyVO toVO(DataMetricDaily m) {
        MetricDailyVO vo = new MetricDailyVO();
        vo.setStatDate(m.getStatDate());
        vo.setMetricKey(m.getMetricKey());
        vo.setMetricValue(m.getMetricValue());
        vo.setDimKey(m.getDimKey());
        return vo;
    }

    private double round(double v, int scale) {
        double factor = Math.pow(10, scale);
        return Math.round(v * factor) / factor;
    }
}