package com.my.petverse.promotion.utils;

import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Component;

import java.util.EnumMap;
import java.util.Map;
import java.util.function.Function;

import static com.my.petverse.promotion.utils.MyLockType.FAIR_LOCK;
import static com.my.petverse.promotion.utils.MyLockType.READ_LOCK;
import static com.my.petverse.promotion.utils.MyLockType.RE_ENTRANT_LOCK;
import static com.my.petverse.promotion.utils.MyLockType.WRITE_LOCK;

/**
 * 锁对象工厂：按锁类型从 Redisson 客户端获取对应的 RLock 实现
 */
@Component
public class MyLockFactory {

    private final Map<MyLockType, Function<String, RLock>> lockHandlers;

    public MyLockFactory(RedissonClient redissonClient) {
        this.lockHandlers = new EnumMap<>(MyLockType.class);
        this.lockHandlers.put(RE_ENTRANT_LOCK, redissonClient::getLock);
        this.lockHandlers.put(FAIR_LOCK, redissonClient::getFairLock);
        this.lockHandlers.put(READ_LOCK, name -> redissonClient.getReadWriteLock(name).readLock());
        this.lockHandlers.put(WRITE_LOCK, name -> redissonClient.getReadWriteLock(name).writeLock());
    }

    public RLock getLock(MyLockType lockType, String name) {
        return lockHandlers.get(lockType).apply(name);
    }
}
