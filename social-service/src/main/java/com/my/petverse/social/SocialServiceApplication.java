package com.my.petverse.social;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.cloud.openfeign.EnableFeignClients;

/**
 * 社交服务启动类（好友关系、聊天）
 * scanBasePackages 扫描 com.my.petverse，使 Common 模块中的配置类和组件生效
 */
@SpringBootApplication(scanBasePackages = "com.my.petverse")
@EnableDiscoveryClient
@EnableFeignClients
public class SocialServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(SocialServiceApplication.class, args);
    }
}
