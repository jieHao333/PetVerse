package com.my.petverse.promotion.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.common.entity.promotion.ExchangeCode;
import com.my.petverse.common.dto.promotion.CodePageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.promotion.ExchangeCodeVO;

/**
 * 兑换码服务类
 */
public interface ExchangeCodeService extends IService<ExchangeCode> {

    /** 异步批量生成兑换码（Redis 序列号 + 加密算法编码） */
    void asyncGenerateCode(Coupon coupon);

    /** 更新兑换标记（BitMap），返回 true 表示原来未兑换（本次设置成功） */
    boolean updateExchangeMark(long serialNum, boolean mark);

    /** 分页查询兑换码 */
    PageResult<ExchangeCodeVO> queryCodePage(CodePageQueryDTO query);

    /** 按序列号查询对应的目标优惠券 id（ZSet 区间索引） */
    Long exchangeTargetId(long serialNum);
}
