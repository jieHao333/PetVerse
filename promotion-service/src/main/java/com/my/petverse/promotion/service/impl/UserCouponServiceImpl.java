package com.my.petverse.promotion.service.impl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.promotion.UserCouponPageQueryDTO;
import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.common.entity.promotion.ExchangeCode;
import com.my.petverse.common.entity.promotion.UserCoupon;
import com.my.petverse.common.enums.ExchangeCodeStatus;
import com.my.petverse.common.enums.UserCouponStatus;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.UserCouponMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.promotion.CouponVO;
import com.my.petverse.promotion.constants.PromotionConstants;
import com.my.petverse.promotion.mapper.CouponMapper;
import com.my.petverse.promotion.mapper.UserCouponMapper;
import com.my.petverse.promotion.service.ExchangeCodeService;
import com.my.petverse.promotion.service.UserCouponService;
import com.my.petverse.promotion.strategy.discount.DiscountStrategy;
import com.my.petverse.promotion.utils.CodeUtil;
import com.my.petverse.promotion.utils.PromotionUtils;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

import static com.my.petverse.promotion.constants.PromotionConstants.COUPON_CODE_MAP_KEY;
import static com.my.petverse.promotion.constants.PromotionConstants.COUPON_RANGE_KEY;

/**
 * 用户优惠券（领取记录）服务实现类。
 * 领券与兑换走 "Redis Lua 原子校验（时间/库存/限领/防重复兑换）→ MQ 异步落库"，
 * 将数据库写入削峰，DB 端再用条件更新防超发兜底
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class UserCouponServiceImpl extends ServiceImpl<UserCouponMapper, UserCoupon> implements UserCouponService {

    private final CouponMapper couponMapper;

    private final ExchangeCodeService codeService;

    private final StringRedisTemplate redisTemplate;

    private final MqEventPublisher mqPublisher;

    private static final RedisScript<Long> RECEIVE_COUPON_SCRIPT;
    private static final RedisScript<String> EXCHANGE_COUPON_SCRIPT;

    static {
        RECEIVE_COUPON_SCRIPT = RedisScript.of(new ClassPathResource("lua/receive_coupon.lua"), Long.class);
        EXCHANGE_COUPON_SCRIPT = RedisScript.of(new ClassPathResource("lua/exchange_coupon.lua"), String.class);
    }

    @Override
    // @MyLock(name = "lock:coupon:#{couponId}")
    public void receiveCoupon(Long couponId) {
        // 1.执行Lua脚本：校验活动时间/库存/限领数量并原子扣减库存
        String key1 = PromotionConstants.COUPON_CACHE_KEY_PREFIX + couponId;
        String key2 = PromotionConstants.USER_COUPON_CACHE_KEY_PREFIX + couponId;
        Long userId = UserContext.getUserId();
        Long r = redisTemplate.execute(RECEIVE_COUPON_SCRIPT, List.of(key1, key2), userId.toString());
        int result = PromotionUtils.null2Zero(r);
        if (result != 0) {
            // 结果大于0，说明出现异常
            throw new BusinessException(ResultCode.BAD_REQUEST,
                    PromotionConstants.RECEIVE_COUPON_ERROR_MSG[result - 1]);
        }
        // 2.发送MQ消息，异步落库
        UserCouponMessage uc = new UserCouponMessage();
        uc.setUserId(userId);
        uc.setCouponId(couponId);
        mqPublisher.publish(MqTopics.PROMOTION_EVENT, MqTopics.TAG_COUPON_RECEIVE, uc,
                "coupon-receive:" + userId + ":" + couponId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void checkAndCreateUserCoupon(UserCouponMessage uc) {
        // 1.查询优惠券
        Coupon coupon = couponMapper.selectById(uc.getCouponId());
        if (coupon == null) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "优惠券不存在！");
        }
        // 2.更新优惠券的已发放数量 +1（条件更新：issue_num < total_num，数据库层防超发兜底）
        int r = couponMapper.incrIssueNum(coupon.getId());
        if (r == 0) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "优惠券库存不足！");
        }
        // 3.新增一个用户券
        saveUserCoupon(coupon, uc.getUserId());

        // 4.更新兑换码状态
        if (uc.getSerialNum() != null) {
            codeService.lambdaUpdate()
                    .set(ExchangeCode::getUserId, uc.getUserId())
                    .set(ExchangeCode::getStatus, ExchangeCodeStatus.USED)
                    .eq(ExchangeCode::getId, uc.getSerialNum())
                    .update();
        }
    }

    @Override
    // @MyLock(name = "lock:coupon:#{T(com.my.petverse.common.context.UserContext).peekUserId()}")
    public void exchangeCoupon(String code) {
        // 1.校验并解析兑换码
        long serialNum = CodeUtil.parseCode(code);
        // 2.执行Lua脚本：BitMap 判断是否已兑换 → ZSet 找到目标优惠券 → 校验时间/限领 → SETBIT 标记已兑换
        Long userId = UserContext.getUserId();
        String result = redisTemplate.execute(
                EXCHANGE_COUPON_SCRIPT,
                List.of(COUPON_CODE_MAP_KEY, COUPON_RANGE_KEY),
                String.valueOf(serialNum), String.valueOf(serialNum + 5000), userId.toString());
        long r = PromotionUtils.parseLong(result);
        if (r < 10) {
            // 异常结果应该是在1~5之间
            throw new BusinessException(ResultCode.BAD_REQUEST,
                    PromotionConstants.EXCHANGE_COUPON_ERROR_MSG[(int) (r - 1)]);
        }
        // 3.发送MQ消息通知，异步落库
        UserCouponMessage uc = new UserCouponMessage();
        uc.setUserId(userId);
        uc.setCouponId(r);
        uc.setSerialNum((int) serialNum);
        mqPublisher.publish(MqTopics.PROMOTION_EVENT, MqTopics.TAG_COUPON_RECEIVE, uc,
                "coupon-receive:" + userId + ":" + r);
    }

    @Override
    public PageResult<CouponVO> queryMyCouponPage(UserCouponPageQueryDTO query) {
        // 1.获取当前用户
        Long userId = UserContext.getUserId();
        // 2.分页查询用户券
        Page<UserCoupon> page = lambdaQuery()
                .eq(UserCoupon::getUserId, userId)
                .eq(UserCoupon::getStatus, UserCouponStatus.of(query.getStatus()))
                .orderByAsc(UserCoupon::getTermEndTime)
                .page(new Page<>(query.getPageNum(), query.getPageSize()));
        List<UserCoupon> records = page.getRecords();
        if (PromotionUtils.isEmpty(records)) {
            return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), PromotionUtils.emptyList());
        }
        // 3.获取优惠券模板信息
        Set<Long> couponIds = records.stream().map(UserCoupon::getCouponId).collect(Collectors.toSet());
        Map<Long, Coupon> couponMap = couponMapper.selectBatchIds(couponIds).stream()
                .collect(Collectors.toMap(Coupon::getId, c -> c));
        // 4.封装VO：以用户券分页结果为准，有效期取用户券的实际有效期（而非模板配置）
        List<CouponVO> list = records.stream()
                .map(uc -> {
                    Coupon coupon = couponMap.get(uc.getCouponId());
                    if (coupon == null) {
                        return null;
                    }
                    CouponVO vo = PromotionUtils.copyBean(coupon, CouponVO.class);
                    vo.setTermEndTime(uc.getTermEndTime());
                    return vo;
                })
                .filter(Objects::nonNull)
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), list);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void writeOffCoupon(List<Long> userCouponIds) {
        // 1.查询优惠券
        List<UserCoupon> userCoupons = listByIds(userCouponIds);
        if (PromotionUtils.isEmpty(userCoupons)) {
            return;
        }
        // 2.处理数据：过滤无效券（状态必须未使用且在有效期内）
        List<UserCoupon> list = userCoupons.stream()
                .filter(coupon -> {
                    if (coupon == null) {
                        return false;
                    }
                    if (UserCouponStatus.UNUSED != coupon.getStatus()) {
                        return false;
                    }
                    LocalDateTime now = LocalDateTime.now();
                    return !now.isBefore(coupon.getTermBeginTime()) && !now.isAfter(coupon.getTermEndTime());
                })
                .map(coupon -> {
                    UserCoupon c = new UserCoupon();
                    c.setId(coupon.getId());
                    c.setStatus(UserCouponStatus.USED);
                    return c;
                })
                .collect(Collectors.toList());

        // 3.核销，修改优惠券状态
        boolean success = updateBatchById(list);
        if (!success) {
            return;
        }
        // 4.更新已使用数量
        List<Long> couponIds = userCoupons.stream().map(UserCoupon::getCouponId).collect(Collectors.toList());
        int c = couponMapper.incrUsedNum(couponIds, 1);
        if (c < 1) {
            throw new BusinessException("更新优惠券使用数量失败！");
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void refundCoupon(List<Long> userCouponIds) {
        // 1.查询优惠券
        List<UserCoupon> userCoupons = listByIds(userCouponIds);
        if (PromotionUtils.isEmpty(userCoupons)) {
            return;
        }
        // 2.处理优惠券数据：已使用的券恢复，按当前时间判断是回到未使用还是已过期
        List<UserCoupon> list = userCoupons.stream()
                .filter(coupon -> coupon != null && UserCouponStatus.USED == coupon.getStatus())
                .map(coupon -> {
                    UserCoupon c = new UserCoupon();
                    c.setId(coupon.getId());
                    LocalDateTime now = LocalDateTime.now();
                    UserCouponStatus status = now.isAfter(coupon.getTermEndTime()) ?
                            UserCouponStatus.EXPIRED : UserCouponStatus.UNUSED;
                    c.setStatus(status);
                    return c;
                }).collect(Collectors.toList());

        // 3.修改优惠券状态
        boolean success = updateBatchById(list);
        if (!success) {
            return;
        }
        // 4.更新已使用数量
        List<Long> couponIds = userCoupons.stream().map(UserCoupon::getCouponId).collect(Collectors.toList());
        int c = couponMapper.incrUsedNum(couponIds, -1);
        if (c < 1) {
            throw new BusinessException("更新优惠券使用数量失败！");
        }
    }

    @Override
    public List<String> queryDiscountRules(List<Long> userCouponIds) {
        // 1.查询优惠券信息
        List<Coupon> coupons = baseMapper.queryCouponByUserCouponIds(userCouponIds, UserCouponStatus.USED);
        if (PromotionUtils.isEmpty(coupons)) {
            return PromotionUtils.emptyList();
        }
        // 2.转换规则
        return coupons.stream()
                .map(c -> DiscountStrategy.getDiscount(c.getDiscountType()).getRule(c))
                .collect(Collectors.toList());
    }

    private void saveUserCoupon(Coupon coupon, Long userId) {
        // 1.基本信息
        UserCoupon uc = new UserCoupon();
        uc.setUserId(userId);
        uc.setCouponId(coupon.getId());
        // 2.有效期信息（未指定有效期时按领取时间 + 有效天数计算）
        LocalDateTime termBeginTime = coupon.getTermBeginTime();
        LocalDateTime termEndTime = coupon.getTermEndTime();
        if (termBeginTime == null) {
            termBeginTime = LocalDateTime.now();
            termEndTime = termBeginTime.plusDays(coupon.getTermDays());
        }
        uc.setTermBeginTime(termBeginTime);
        uc.setTermEndTime(termEndTime);
        // 3.保存
        save(uc);
    }
}
