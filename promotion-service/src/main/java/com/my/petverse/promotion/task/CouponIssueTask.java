package com.my.petverse.promotion.task;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.common.enums.CouponStatus;
import com.my.petverse.promotion.service.CouponService;
import com.my.petverse.promotion.utils.PromotionUtils;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 优惠券定时发放任务：扫描状态为"未开始"且已到发放开始时间的优惠券，批量置为"发放中"并写入缓存。
 * 每批处理后这些券已不满足筛选条件，因此始终查询第 1 页直到没有数据；
 * 多实例部署时重复处理仅重复写缓存，状态更新幂等
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class CouponIssueTask {

    /** 每批处理的优惠券数量 */
    private static final int BATCH_SIZE = 20;

    /** 单轮最多处理的批次数，避免异常情况下无限循环 */
    private static final int MAX_BATCHES = 100;

    private final CouponService couponService;

    @Scheduled(fixedDelay = 30_000L, initialDelay = 30_000L)
    public void handleCouponIssueJob() {
        try {
            for (int i = 0; i < MAX_BATCHES; i++) {
                // 查询"未开始"且已到发放时间的优惠券
                Page<Coupon> page = couponService.lambdaQuery()
                        .eq(Coupon::getStatus, CouponStatus.UN_ISSUE)
                        .le(Coupon::getIssueBeginTime, LocalDateTime.now())
                        .page(new Page<>(1, BATCH_SIZE));
                List<Coupon> records = page.getRecords();
                if (PromotionUtils.isEmpty(records)) {
                    return;
                }
                couponService.beginIssueBatch(records);
                if (records.size() < BATCH_SIZE) {
                    return;
                }
            }
        } catch (Exception e) {
            // 失败仅记日志，等待下一轮重试
            log.error("优惠券定时发放任务执行失败", e);
        }
    }
}
