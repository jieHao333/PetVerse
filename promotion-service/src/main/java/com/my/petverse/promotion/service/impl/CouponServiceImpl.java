package com.my.petverse.promotion.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.promotion.CouponFormDTO;
import com.my.petverse.common.dto.promotion.CouponIssueFormDTO;
import com.my.petverse.common.dto.promotion.CouponPageQueryDTO;
import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.common.entity.promotion.CouponScope;
import com.my.petverse.common.entity.promotion.UserCoupon;
import com.my.petverse.common.enums.CouponStatus;
import com.my.petverse.common.enums.DiscountType;
import com.my.petverse.common.enums.ObtainType;
import com.my.petverse.common.enums.UserCouponStatus;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.promotion.CouponDetailVO;
import com.my.petverse.common.vo.promotion.CouponPageVO;
import com.my.petverse.common.vo.promotion.CouponScopeVO;
import com.my.petverse.common.vo.promotion.CouponVO;
import com.my.petverse.promotion.constants.PromotionConstants;
import com.my.petverse.promotion.constants.ScopeType;
import com.my.petverse.promotion.mapper.CouponMapper;
import com.my.petverse.promotion.service.CouponScopeService;
import com.my.petverse.promotion.service.CouponService;
import com.my.petverse.promotion.service.ExchangeCodeService;
import com.my.petverse.promotion.service.UserCouponService;
import com.my.petverse.promotion.strategy.scope.ScopeNameHandler;
import com.my.petverse.promotion.utils.PromotionUtils;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.redis.connection.StringRedisConnection;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import static com.my.petverse.common.enums.CouponStatus.DRAFT;
import static com.my.petverse.common.enums.CouponStatus.ISSUING;
import static com.my.petverse.common.enums.CouponStatus.PAUSE;
import static com.my.petverse.common.enums.CouponStatus.UN_ISSUE;

/**
 * 优惠券（规则信息）服务实现类
 */
@Slf4j
@Service
public class CouponServiceImpl extends ServiceImpl<CouponMapper, Coupon> implements CouponService {

    private final CouponScopeService scopeService;

    private final ExchangeCodeService codeService;

    private final UserCouponService userCouponService;

    private final Map<ScopeType, ScopeNameHandler> scopeNameHandlers;

    private final StringRedisTemplate redisTemplate;

    public CouponServiceImpl(CouponScopeService scopeService,
                             ExchangeCodeService codeService,
                             UserCouponService userCouponService,
                             @Qualifier(ScopeType.CATEGORY_HANDLER_NAME) ScopeNameHandler categoryScopeNameHandler,
                             @Qualifier(ScopeType.PRODUCT_HANDLER_NAME) ScopeNameHandler productScopeNameHandler,
                             StringRedisTemplate redisTemplate) {
        this.scopeService = scopeService;
        this.codeService = codeService;
        this.userCouponService = userCouponService;
        this.scopeNameHandlers = new EnumMap<>(ScopeType.class);
        this.scopeNameHandlers.put(ScopeType.CATEGORY, categoryScopeNameHandler);
        this.scopeNameHandlers.put(ScopeType.PRODUCT, productScopeNameHandler);
        this.redisTemplate = redisTemplate;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void saveCoupon(CouponFormDTO dto) {
        // 1.保存优惠券
        Coupon coupon = PromotionUtils.copyBean(dto, Coupon.class);
        save(coupon);

        if (!Boolean.TRUE.equals(dto.getSpecific())) {
            // 没有范围限定
            return;
        }
        Long couponId = coupon.getId();
        // 2.保存限定范围
        List<Long> scopes = dto.getScopes();
        if (PromotionUtils.isEmpty(scopes)) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "限定范围不能为空");
        }
        int scopeType = dto.getScopeType() == null ? ScopeType.CATEGORY.getValue() : dto.getScopeType();
        List<CouponScope> list = scopes.stream()
                .map(bizId -> new CouponScope().setBizId(bizId).setCouponId(couponId).setType(scopeType))
                .collect(Collectors.toList());
        scopeService.saveBatch(list);
    }

    @Override
    public PageResult<CouponPageVO> queryCouponByPage(CouponPageQueryDTO query) {
        DiscountType type = DiscountType.of(query.getType());
        CouponStatus status = CouponStatus.of(query.getStatus());
        String name = query.getName();
        // 1.分页查询
        Page<Coupon> page = lambdaQuery()
                .eq(type != null, Coupon::getDiscountType, type)
                .eq(status != null, Coupon::getStatus, status)
                .like(StringUtils.hasText(name), Coupon::getName, name)
                .orderByDesc(Coupon::getCreateTime)
                .page(new Page<>(query.getPageNum(), query.getPageSize()));
        // 2.处理VO
        List<Coupon> records = page.getRecords();
        if (PromotionUtils.isEmpty(records)) {
            return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), PromotionUtils.emptyList());
        }
        List<CouponPageVO> list = PromotionUtils.copyList(records, CouponPageVO.class);
        // 3.返回
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), list);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void beginIssue(CouponIssueFormDTO dto) {
        // 1.查询优惠券
        Coupon coupon = getById(dto.getId());
        if (coupon == null) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "优惠券不存在！");
        }
        // 2.判断优惠券状态，是否是暂停或待发放
        if (coupon.getStatus() != DRAFT && coupon.getStatus() != PAUSE) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "优惠券状态错误！");
        }
        // 3.判断是否是立刻发放
        LocalDateTime issueBeginTime = dto.getIssueBeginTime();
        LocalDateTime now = LocalDateTime.now();
        boolean isBegin = issueBeginTime == null || !issueBeginTime.isAfter(now);
        // 4.更新优惠券
        Coupon c = PromotionUtils.copyBean(dto, Coupon.class);
        if (isBegin) {
            c.setStatus(ISSUING);
            c.setIssueBeginTime(now);
        } else {
            c.setStatus(UN_ISSUE);
        }
        updateById(c);

        // 5.添加缓存
        if (isBegin) {
            coupon.setIssueBeginTime(c.getIssueBeginTime());
            coupon.setIssueEndTime(c.getIssueEndTime());
            cacheCouponInfo(coupon);
        }

        // 6.判断是否需要生成兑换码：优惠券类型必须是兑换码，且原状态必须是待发放
        if (coupon.getObtainWay() == ObtainType.ISSUE && coupon.getStatus() == DRAFT) {
            coupon.setIssueEndTime(c.getIssueEndTime());
            codeService.asyncGenerateCode(coupon);
        }
    }

    private void cacheCouponInfo(Coupon coupon) {
        // 1.组织数据
        Map<String, String> map = new HashMap<>(4);
        map.put("issueBeginTime", String.valueOf(PromotionUtils.toEpochSecond(coupon.getIssueBeginTime())));
        map.put("issueEndTime", String.valueOf(PromotionUtils.toEpochSecond(coupon.getIssueEndTime())));
        map.put("totalNum", String.valueOf(coupon.getTotalNum()));
        map.put("userLimit", String.valueOf(coupon.getUserLimit()));
        // 2.写缓存
        redisTemplate.opsForHash().putAll(PromotionConstants.COUPON_CACHE_KEY_PREFIX + coupon.getId(), map);
    }

    @Override
    public List<CouponVO> queryIssuingCoupons() {
        // 1.查询发放中的优惠券列表
        List<Coupon> coupons = lambdaQuery()
                .eq(Coupon::getStatus, ISSUING)
                .eq(Coupon::getObtainWay, ObtainType.PUBLIC)
                .list();
        if (PromotionUtils.isEmpty(coupons)) {
            return PromotionUtils.emptyList();
        }
        // 2.统计当前用户已经领取的优惠券的信息
        List<Long> couponIds = coupons.stream().map(Coupon::getId).collect(Collectors.toList());
        List<UserCoupon> userCoupons = userCouponService.lambdaQuery()
                .eq(UserCoupon::getUserId, UserContext.getUserId())
                .in(UserCoupon::getCouponId, couponIds)
                .list();
        // 2.1.统计当前用户对每张券的已领取数量
        Map<Long, Long> issuedMap = userCoupons.stream()
                .collect(Collectors.groupingBy(UserCoupon::getCouponId, Collectors.counting()));
        // 2.2.统计当前用户对每张券的已领取且未使用的数量
        Map<Long, Long> unusedMap = userCoupons.stream()
                .filter(uc -> uc.getStatus() == UserCouponStatus.UNUSED)
                .collect(Collectors.groupingBy(UserCoupon::getCouponId, Collectors.counting()));
        // 3.封装VO结果
        List<CouponVO> list = new ArrayList<>(coupons.size());
        for (Coupon c : coupons) {
            CouponVO vo = PromotionUtils.copyBean(c, CouponVO.class);
            list.add(vo);
            // 3.1.是否可以领取：已领取数量 < 总数量 && 当前用户已领数量 < 每人限领数量
            vo.setAvailable(
                    c.getIssueNum() < c.getTotalNum()
                            && issuedMap.getOrDefault(c.getId(), 0L) < c.getUserLimit()
            );
            // 3.2.是否可以使用：当前用户已领取且未使用的数量 > 0
            vo.setReceived(unusedMap.getOrDefault(c.getId(), 0L) > 0);
        }
        return list;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void pauseIssue(Long id) {
        // 1.查询旧优惠券
        Coupon coupon = getById(id);
        if (coupon == null) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "优惠券不存在");
        }
        // 2.当前券状态必须是未开始或进行中
        CouponStatus status = coupon.getStatus();
        if (status != UN_ISSUE && status != ISSUING) {
            // 状态错误，直接结束
            return;
        }
        // 3.更新状态
        boolean success = lambdaUpdate()
                .set(Coupon::getStatus, PAUSE)
                .eq(Coupon::getId, id)
                .in(Coupon::getStatus, UN_ISSUE, ISSUING)
                .update();
        if (!success) {
            // 可能是重复更新，结束
            log.error("重复暂停优惠券");
        }
        // 4.删除缓存
        redisTemplate.delete(PromotionConstants.COUPON_CACHE_KEY_PREFIX + id);
    }

    @Override
    public void deleteById(Long id) {
        // 1.查询
        Coupon coupon = getById(id);
        if (coupon == null || coupon.getStatus() != DRAFT) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "优惠券不存在或者优惠券正在使用中");
        }
        // 2.删除优惠券
        boolean success = remove(new LambdaQueryWrapper<Coupon>()
                .eq(Coupon::getId, id)
                .eq(Coupon::getStatus, DRAFT)
        );
        if (!success) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "优惠券不存在或者优惠券正在使用中");
        }
        // 3.删除优惠券对应限定范围
        if (!Boolean.TRUE.equals(coupon.getSpecific())) {
            return;
        }
        scopeService.remove(new LambdaQueryWrapper<CouponScope>().eq(CouponScope::getCouponId, id));
    }

    @Override
    public CouponDetailVO queryCouponById(Long id) {
        // 1.查询优惠券
        Coupon coupon = getById(id);
        // 2.转换VO
        CouponDetailVO vo = PromotionUtils.copyBean(coupon, CouponDetailVO.class);
        if (vo == null || !Boolean.TRUE.equals(coupon.getSpecific())) {
            // 数据不存在，或者没有限定范围，直接结束
            return vo;
        }
        // 3.查询限定范围
        List<CouponScope> scopes = scopeService.lambdaQuery().eq(CouponScope::getCouponId, id).list();
        if (PromotionUtils.isEmpty(scopes)) {
            return vo;
        }
        // 3.1.按范围类型分组，分别获取分类名/商品名
        Map<Integer, List<CouponScope>> scopeGroups = scopes.stream()
                .collect(Collectors.groupingBy(CouponScope::getType));
        List<CouponScopeVO> scopeVOS = new ArrayList<>(scopes.size());
        for (Map.Entry<Integer, List<CouponScope>> entry : scopeGroups.entrySet()) {
            ScopeType scopeType = ScopeType.of(entry.getKey());
            ScopeNameHandler handler = scopeNameHandlers.get(scopeType);
            if (handler == null) {
                continue;
            }
            List<Long> bizIds = entry.getValue().stream().map(CouponScope::getBizId).collect(Collectors.toList());
            List<String> names = handler.getNameByIds(bizIds);
            for (int i = 0; i < bizIds.size(); i++) {
                String name = i < names.size() ? names.get(i) : String.valueOf(bizIds.get(i));
                scopeVOS.add(new CouponScopeVO(bizIds.get(i), name));
            }
        }
        vo.setScopes(scopeVOS);
        return vo;
    }

    @Override
    public void beginIssueBatch(List<Coupon> coupons) {
        // 1.更新券状态
        for (Coupon c : coupons) {
            c.setStatus(ISSUING);
        }
        updateBatchById(coupons);
        // 2.批量缓存（pipeline 一次网络往返写入全部 Hash）
        redisTemplate.executePipelined((RedisCallback<Object>) connection -> {
            StringRedisConnection src = (StringRedisConnection) connection;
            for (Coupon coupon : coupons) {
                Map<String, String> map = new HashMap<>(4);
                map.put("issueBeginTime", String.valueOf(PromotionUtils.toEpochSecond(coupon.getIssueBeginTime())));
                map.put("issueEndTime", String.valueOf(PromotionUtils.toEpochSecond(coupon.getIssueEndTime())));
                map.put("totalNum", String.valueOf(coupon.getTotalNum()));
                map.put("userLimit", String.valueOf(coupon.getUserLimit()));
                src.hMSet(PromotionConstants.COUPON_CACHE_KEY_PREFIX + coupon.getId(), map);
            }
            return null;
        });
    }
}
