package com.my.petverse.data.task;

import com.my.petverse.data.service.DataAssetService;
import com.my.petverse.data.service.DataMetricService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDate;

/**
 * 数据中台微批聚合任务：周期性把 DWD 行为明细聚合为画像、商品统计、反馈样本与按日指标。
 * 单实例假设、按唯一键幂等，可重复执行；每轮异常仅记录日志，不影响下一轮。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class DataAggregateTask {

    private final DataAssetService assetService;
    private final DataMetricService metricService;

    @Scheduled(fixedDelayString = "${petverse.data.aggregate-interval-ms:120000}", initialDelay = 30000)
    public void aggregate() {
        try {
            assetService.aggregate();
            metricService.aggregate(LocalDate.now());
        } catch (Exception e) {
            log.error("数据中台微批聚合失败", e);
        }
    }
}