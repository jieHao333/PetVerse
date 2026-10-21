package com.my.petverse.promotion.constants;

/**
 * 促销服务 Redis Key 与错误消息常量
 */
public interface PromotionConstants {

    /** 兑换码序列号自增键（INCR 生成全局递增序列号） */
    String COUPON_CODE_SERIAL_KEY = "coupon:code:serial";

    /** 兑换码已兑换标记（BitMap，位偏移 = 兑换码序列号） */
    String COUPON_CODE_MAP_KEY = "coupon:code:map";

    /** 优惠券发放信息缓存前缀（Hash：issueBeginTime/issueEndTime/totalNum/userLimit） */
    String COUPON_CACHE_KEY_PREFIX = "prs:coupon:";

    /** 用户领券计数缓存前缀（Hash：field=userId，value=已领数量） */
    String USER_COUPON_CACHE_KEY_PREFIX = "prs:user:coupon:";

    /** 兑换码序列号 → 优惠券 id 的区间索引（ZSet，member=couponId，score=最大序列号） */
    String COUPON_RANGE_KEY = "coupon:code:range";

    /** 领券 Lua 脚本错误消息（返回值 1~4 对应下标 0~3） */
    String[] RECEIVE_COUPON_ERROR_MSG = {
            "活动未开始",
            "库存不足",
            "活动已经结束",
            "领取次数过多",
    };

    /** 兑换码 Lua 脚本错误消息（返回值 1~5 对应下标 0~4） */
    String[] EXCHANGE_COUPON_ERROR_MSG = {
            "兑换码已兑换",
            "无效兑换码",
            "活动未开始",
            "活动已经结束",
            "领取次数过多",
    };

    /** 兑换码格式（Base32 自定义码表，50bit 明文 → 10 位字符） */
    String COUPON_CODE_PATTERN = "^[6CSB7H8DAKXZF3N95RTMVUQG2YE4JWPL]{10}$";
}
