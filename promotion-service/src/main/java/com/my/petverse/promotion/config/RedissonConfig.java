package com.my.petverse.promotion.config;

import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.config.Config;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Redisson 客户端配置，复用 spring.data.redis 连接参数，供 MyLock 注解式分布式锁使用
 */
@Configuration
public class RedissonConfig {

    @Value("${spring.data.redis.host:localhost}")
    private String host;

    @Value("${spring.data.redis.port:6379}")
    private int port;

    @Value("${spring.data.redis.password:}")
    private String password;

    @Value("${spring.data.redis.database:0}")
    private int database;

    @Bean(destroyMethod = "shutdown")
    public RedissonClient redissonClient() {
        Config config = new Config();
        // 懒初始化：应用启动时不建立 Redis 连接，首次使用分布式锁时才连接，
        // 避免 Redis 暂时不可用导致服务启动失败
        config.setLazyInitialization(true);
        String address = "redis://" + host + ":" + port;
        if (password == null || password.isBlank()) {
            config.useSingleServer()
                    .setAddress(address)
                    .setDatabase(database);
        } else {
            config.useSingleServer()
                    .setAddress(address)
                    .setPassword(password)
                    .setDatabase(database);
        }
        return Redisson.create(config);
    }
}
