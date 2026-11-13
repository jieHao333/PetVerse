package com.my.petverse.data.task;

import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.data.service.DataModelService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 重排模型定时训练任务：周期性用最新反馈样本训练并注册新版本。
 * 样本不足属正常情况（业务异常），降级为调试日志；其余异常仅记录，不影响下一轮。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class DataModelTrainTask {

    private final DataModelService modelService;

    @Scheduled(fixedDelayString = "${petverse.data.train-interval-ms:1800000}", initialDelay = 120000)
    public void train() {
        try {
            modelService.train();
        } catch (BusinessException e) {
            log.debug("重排模型训练跳过：{}", e.getMessage());
        } catch (Exception e) {
            log.error("重排模型训练失败", e);
        }
    }
}