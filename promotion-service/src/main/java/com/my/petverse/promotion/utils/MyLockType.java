package com.my.petverse.promotion.utils;

/** 分布式锁类型 */
public enum MyLockType {
    /** 可重入锁 */
    RE_ENTRANT_LOCK,
    /** 公平锁 */
    FAIR_LOCK,
    /** 读锁（共享） */
    READ_LOCK,
    /** 写锁（排他） */
    WRITE_LOCK
}
