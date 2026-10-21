package com.my.petverse.promotion;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.cloud.openfeign.EnableFeignClients;
import org.springframework.context.annotation.EnableAspectJAutoProxy;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * 促销服务启动类（优惠券发放/领取/兑换码/最优优惠方案计算）
 * scanBasePackages 扫描 com.my.petverse，使 common 模块中的配置类和组件生效
 */
@SpringBootApplication(scanBasePackages = "com.my.petverse")
@EnableDiscoveryClient
@EnableFeignClients
@EnableScheduling
// 异步：兑换码批量生成走独立线程池
@EnableAsync
// AOP 暴露代理，供策略模式自调用场景下切面生效（MyLock 分布式锁）
@EnableAspectJAutoProxy(exposeProxy = true)
@MapperScan("com.my.petverse.promotion.mapper")
public class PromotionServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(PromotionServiceApplication.class, args);
    }
}
