package com.my.petverse.pet.support;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.List;

/**
 * 每日签到 BitMap 闸门
 *
 * 存储布局：key = signin:{userId}:{yyyyMM}（用户 + 月份一个 key），
 * bit 偏移 = 当月第几天 - 1，单个用户单月最多占 4 字节；
 * 偏移量使用月内天数，bit 位密度与月份天数对齐，key 之间按用户隔离。
 *
 * 并发语义：SETBIT 原子返回该 bit 的原值，0 表示本次抢占成功、1 表示当天已签到，
 * 单条命令完成判重与落位；TTL 在抢占成功时刷新，
 * 保留当月及次月上旬的数据，避免 key 长期累积。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class SignInBitMap {

    private final StringRedisTemplate redis;

    /** key 前缀与时间格式：signin:{userId}:{yyyyMM} */
    private static final String KEY_PREFIX = "signin";

    private static final DateTimeFormatter MONTH_FORMAT = DateTimeFormatter.ofPattern("yyyyMM");

    /** 抢占 bit 并刷新 TTL，返回该 bit 的原值（0 表示本次抢占成功） */
    private static final RedisScript<Long> CLAIM_SCRIPT = new DefaultRedisScript<>(
            "local old = redis.call('SETBIT', KEYS[1], ARGV[1], 1) "
                    + "if old == 0 then redis.call('PEXPIRE', KEYS[1], ARGV[2]) end "
                    + "return old", Long.class);

    /** 统计当月已签到天数 */
    private static final RedisScript<Long> MONTH_COUNT_SCRIPT = new DefaultRedisScript<>(
            "return redis.call('BITCOUNT', KEYS[1])", Long.class);

    /** key 保留时长：40 天，覆盖当月与次月月初 */
    private static final long KEY_TTL_MILLIS = 40L * 24 * 60 * 60 * 1000;

    /**
     * 抢占当日签到位
     *
     * @return true 表示本次抢占成功（今天尚未签到），false 表示当天已签到
     */
    public boolean claim(Long userId, LocalDate date) {
        try {
            Long previous = redis.execute(CLAIM_SCRIPT, List.of(monthKey(userId, date)),
                    String.valueOf(date.getDayOfMonth() - 1), String.valueOf(KEY_TTL_MILLIS));
            return previous != null && previous == 0L;
        } catch (Exception e) {
            // Redis 不可用时放行：调用方仍会校验宠物档案上的最近签到日期，功能不中断
            log.warn("签到 BitMap 闸门不可用，本次由数据库签到状态兜底，userId={}", userId, e);
            return true;
        }
    }

    /** 查询当月已签到天数，Redis 不可用时返回 0（仅用于展示，不影响签到结果） */
    public long monthSignedDays(Long userId, LocalDate date) {
        try {
            Long count = redis.execute(MONTH_COUNT_SCRIPT, List.of(monthKey(userId, date)));
            return count == null ? 0L : count;
        } catch (Exception e) {
            log.warn("查询签到 BitMap 失败，userId={}", userId, e);
            return 0L;
        }
    }

    private String monthKey(Long userId, LocalDate date) {
        return KEY_PREFIX + ":" + userId + ":" + date.format(MONTH_FORMAT);
    }
}