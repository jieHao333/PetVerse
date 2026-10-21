package com.my.petverse.promotion.utils;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import java.util.concurrent.TimeUnit;

/**
 * 声明式分布式锁注解，标注在需要互斥执行的方法上，由 MyLockAspect 切面代理执行
 */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface MyLock {

    /** 锁名称，支持 SpEL */
    String name();

    /** 获取锁的最大等待时间 */
    long waitTime() default 1;

    /** 锁自动释放时间，-1 表示启用看门狗自动续期 */
    long leaseTime() default -1;

    /** 时间单位 */
    TimeUnit unit() default TimeUnit.SECONDS;

    /** 锁类型 */
    MyLockType lockType() default MyLockType.RE_ENTRANT_LOCK;

    /** 获取锁失败时的处理策略 */
    MyLockStrategy lockStrategy() default MyLockStrategy.FAIL_AFTER_RETRY_TIMEOUT;
}
