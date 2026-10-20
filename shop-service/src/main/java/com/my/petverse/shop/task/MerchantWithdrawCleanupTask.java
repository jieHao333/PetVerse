package com.my.petverse.shop.task;

import com.my.petverse.shop.service.MerchantService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 商家撤销冷静期到期清理任务：定期扫描已过冷静期且仍未撤回的商家，
 * 彻底清除店铺、商品、评价与购物车关联数据，并降级用户角色
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class MerchantWithdrawCleanupTask {

    private final MerchantService merchantService;

    /** 每 10 分钟扫描一次到期商家，批量上限由服务内部限制，多实例并行执行也安全 */
    @Scheduled(fixedDelay = 10 * 60_000L, initialDelay = 60_000L)
    public void purgeExpiredWithdrawals() {
        try {
            int purged = merchantService.purgeExpiredWithdrawals();
            if (purged > 0) {
                log.info("商家撤销冷静期到期清理完成，本轮清理 {} 家", purged);
            }
        } catch (Exception e) {
            log.error("商家撤销冷静期到期清理失败，下一轮重试", e);
        }
    }
}
