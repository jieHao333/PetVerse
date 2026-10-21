package com.my.petverse.promotion.utils;

import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;
import org.redisson.api.RLock;

/**
 * 获取锁失败时的处理策略
 */
public enum MyLockStrategy {

    /** 不等待，立即结束（返回 null） */
    SKIP_FAST() {
        @Override
        public boolean tryLock(RLock lock, MyLock prop) throws InterruptedException {
            return lock.tryLock(0, prop.leaseTime(), prop.unit());
        }
    },
    /** 不等待，失败抛异常 */
    FAIL_FAST() {
        @Override
        public boolean tryLock(RLock lock, MyLock prop) throws InterruptedException {
            boolean isLock = lock.tryLock(0, prop.leaseTime(), prop.unit());
            if (!isLock) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "请求太频繁");
            }
            return true;
        }
    },
    /** 阻塞等待直到获取锁 */
    KEEP_TRYING() {
        @Override
        public boolean tryLock(RLock lock, MyLock prop) {
            lock.lock(prop.leaseTime(), prop.unit());
            return true;
        }
    },
    /** 在等待时间内重试，超时后结束（返回 null） */
    SKIP_AFTER_RETRY_TIMEOUT() {
        @Override
        public boolean tryLock(RLock lock, MyLock prop) throws InterruptedException {
            return lock.tryLock(prop.waitTime(), prop.leaseTime(), prop.unit());
        }
    },
    /** 在等待时间内重试，超时后抛异常 */
    FAIL_AFTER_RETRY_TIMEOUT() {
        @Override
        public boolean tryLock(RLock lock, MyLock prop) throws InterruptedException {
            boolean isLock = lock.tryLock(prop.waitTime(), prop.leaseTime(), prop.unit());
            if (!isLock) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "请求太频繁");
            }
            return true;
        }
    };

    public abstract boolean tryLock(RLock lock, MyLock prop) throws InterruptedException;
}
