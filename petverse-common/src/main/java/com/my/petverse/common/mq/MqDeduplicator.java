package com.my.petverse.common.mq;

import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 消费端内存幂等去重器：RocketMQ 至少投递一次，消费端按业务键去重。
 * 适用于单实例消费、重复投递窗口较短的场景（如经验发放、角色升级）；
 * 条目超过容量上限或存活超过 12 小时后自动淘汰
 */
@Component
public class MqDeduplicator {

    /** 最多保留的去重键数量 */
    private static final int MAX_ENTRIES = 10_000;

    /** 去重键存活时长（毫秒） */
    private static final long TTL_MILLIS = 12 * 60 * 60 * 1000L;

    private final Map<String, Long> seen = Collections.synchronizedMap(
            new LinkedHashMap<>(256, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, Long> eldest) {
                    return size() > MAX_ENTRIES
                            || System.currentTimeMillis() - eldest.getValue() > TTL_MILLIS;
                }
            });

    /**
     * 判断业务键是否首次出现：首次返回 true 并登记，重复出现返回 false
     */
    public boolean firstTime(String key) {
        if (key == null) {
            return true;
        }
        synchronized (seen) {
            return seen.putIfAbsent(key, System.currentTimeMillis()) == null;
        }
    }

    /**
     * 释放业务键：消费处理失败需要依赖 RocketMQ 重试时调用，
     * 避免重试消息被去重器拦截导致业务永久丢失
     */
    public void release(String key) {
        if (key == null) {
            return;
        }
        synchronized (seen) {
            seen.remove(key);
        }
    }
}
