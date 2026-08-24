package com.my.petverse.space;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.cloud.openfeign.EnableFeignClients;

/**
 * 宠域空间服务启动类
 * scanBasePackages 扫描 com.my.petverse，使 common 模块中的配置类和组件生效
 */
@SpringBootApplication(scanBasePackages = "com.my.petverse")
@EnableDiscoveryClient
@EnableFeignClients
public class SpaceServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(SpaceServiceApplication.class, args);
    }
}
