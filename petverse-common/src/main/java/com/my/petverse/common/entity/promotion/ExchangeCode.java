package com.my.petverse.common.entity.promotion;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.enums.ExchangeCodeStatus;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 兑换码实体，对应数据库表 exchange_code
 * 主键为全局递增的序列号（Redis 自增生成），兑换码由序列号 + 优惠券 id 经加权校验与异或混淆后 Base32 编码得到
 */
@Data
@Accessors(chain = true)
@TableName("exchange_code")
public class ExchangeCode implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 兑换码序列号（主键，Redis 自增生成，兑换码编码时作为载荷） */
    @TableId(value = "id", type = IdType.INPUT)
    private Integer id;

    /** 兑换码（Base32 编码后的 10 位字符串） */
    private String code;

    /** 兑换码状态：1-待兑换 2-已兑换 3-兑换活动已结束 */
    private ExchangeCodeStatus status;

    /** 兑换人（兑换后回填） */
    private Long userId;

    /** 兑换类型：1-优惠券（保留字段） */
    private Integer type;

    /** 兑换目标 id（优惠券 id） */
    private Long exchangeTargetId;

    /** 创建时间 */
    private LocalDateTime createTime;

    /** 兑换码过期时间 */
    private LocalDateTime expiredTime;

    /** 更新时间 */
    private LocalDateTime updateTime;
}
