package com.my.petverse.promotion.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.promotion.CodePageQueryDTO;
import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.common.entity.promotion.ExchangeCode;
import com.my.petverse.common.enums.ExchangeCodeStatus;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.promotion.ExchangeCodeVO;
import com.my.petverse.promotion.mapper.ExchangeCodeMapper;
import com.my.petverse.promotion.service.ExchangeCodeService;
import com.my.petverse.promotion.utils.CodeUtil;
import com.my.petverse.promotion.utils.PromotionUtils;
import org.springframework.data.redis.core.BoundValueOperations;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import static com.my.petverse.promotion.constants.PromotionConstants.COUPON_CODE_MAP_KEY;
import static com.my.petverse.promotion.constants.PromotionConstants.COUPON_CODE_SERIAL_KEY;
import static com.my.petverse.promotion.constants.PromotionConstants.COUPON_RANGE_KEY;

/**
 * 兑换码服务实现类：Redis INCR 生成全局递增序列号，
 * 序列号 + 优惠券 id 经加权校验与异或混淆后 Base32 编码为 10 位兑换码
 */
@Service
public class ExchangeCodeServiceImpl extends ServiceImpl<ExchangeCodeMapper, ExchangeCode> implements ExchangeCodeService {

    private final StringRedisTemplate redisTemplate;
    private final BoundValueOperations<String, String> serialOps;

    public ExchangeCodeServiceImpl(StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
        this.serialOps = redisTemplate.boundValueOps(COUPON_CODE_SERIAL_KEY);
    }

    @Override
    @Async("generateExchangeCodeExecutor")
    public void asyncGenerateCode(Coupon coupon) {
        // 发放数量
        Integer totalNum = coupon.getTotalNum();
        // 1.获取Redis自增序列号（一次 INCR 增加总量，得到本批最大序列号）
        Long result = serialOps.increment(totalNum);
        if (result == null) {
            return;
        }
        int maxSerialNum = result.intValue();
        List<ExchangeCode> list = new ArrayList<>(totalNum);
        for (int serialNum = maxSerialNum - totalNum + 1; serialNum <= maxSerialNum; serialNum++) {
            // 2.生成兑换码
            String code = CodeUtil.generateCode(serialNum, coupon.getId());
            ExchangeCode e = new ExchangeCode();
            e.setCode(code);
            e.setId(serialNum);
            e.setExchangeTargetId(coupon.getId());
            e.setExpiredTime(coupon.getIssueEndTime());
            list.add(e);
        }
        // 3.保存数据库
        saveBatch(list);
        // 4.写入Redis缓存：ZSet，member 为 couponId，score 为该券兑换码的最大序列号
        //（兑换时按序列号区间反查所属优惠券）
        redisTemplate.opsForZSet().add(COUPON_RANGE_KEY, coupon.getId().toString(), maxSerialNum);
    }

    @Override
    public boolean updateExchangeMark(long serialNum, boolean mark) {
        Boolean boo = redisTemplate.opsForValue().setBit(COUPON_CODE_MAP_KEY, serialNum, mark);
        return boo != null && boo;
    }

    @Override
    public PageResult<ExchangeCodeVO> queryCodePage(CodePageQueryDTO query) {
        // 1.分页查询兑换码
        Page<ExchangeCode> page = lambdaQuery()
                .eq(ExchangeCode::getStatus, ExchangeCodeStatus.of(query.getStatus()))
                .eq(ExchangeCode::getExchangeTargetId, query.getCouponId())
                .page(new Page<>(query.getPageNum(), query.getPageSize()));
        // 2.返回数据
        List<ExchangeCodeVO> records = page.getRecords().stream()
                .map(c -> new ExchangeCodeVO(c.getId(), c.getCode()))
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), records);
    }

    @Override
    public Long exchangeTargetId(long serialNum) {
        // 1.查询score值比当前序列号大的第一个优惠券（ZSet 区间索引）
        Set<String> results = redisTemplate.opsForZSet().rangeByScore(
                COUPON_RANGE_KEY, serialNum, serialNum + 5000, 0L, 1L);
        if (PromotionUtils.isEmpty(results)) {
            return null;
        }
        // 2.数据转换
        String next = results.iterator().next();
        return Long.parseLong(next);
    }
}
