package com.my.petverse.data;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * 数据中台服务启动类。
 * scanBasePackages 扫描 com.my.petverse，使 common 模块中的配置类与组件生效；
 * 采集消费者复用 common 的 RabbitMQ 基础设施，聚合任务由 @EnableScheduling 驱动。
 */
@SpringBootApplication(scanBasePackages = "com.my.petverse")
@EnableDiscoveryClient
@EnableScheduling
public class DataServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(DataServiceApplication.class, args);
    }
}