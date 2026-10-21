package com.my.petverse.promotion.service.impl;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.promotion.CouponDiscountDTO;
import com.my.petverse.common.dto.promotion.OrderCouponDTO;
import com.my.petverse.common.dto.promotion.OrderProductDTO;
import com.my.petverse.common.entity.promotion.Coupon;
import com.my.petverse.common.entity.promotion.CouponScope;
import com.my.petverse.common.enums.UserCouponStatus;
import com.my.petverse.promotion.constants.ScopeType;
import com.my.petverse.promotion.mapper.UserCouponMapper;
import com.my.petverse.promotion.service.CouponScopeService;
import com.my.petverse.promotion.service.DiscountService;
import com.my.petverse.promotion.strategy.discount.Discount;
import com.my.petverse.promotion.strategy.discount.DiscountStrategy;
import com.my.petverse.promotion.utils.PermuteUtil;
import com.my.petverse.promotion.utils.PromotionUtils;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

/**
 * 优惠方案计算服务实现类（智能最优方案搜索）：
 * 1. 初筛：按订单总价判断每张可用券是否达到门槛
 * 2. 细筛：按券的限定范围筛选可用商品，判断范围总价是否达到门槛
 * 3. 全排列枚举所有用券顺序（叠加优惠与顺序相关）+ 单券方案
 * 4. 线程池并行计算每个方案的优惠明细（按商品价格占比分摊）
 * 5. 双维度筛选最优解（用券相同取优惠最大 & 优惠相同取用券最少），求交集后按优惠金额降序
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DiscountServiceImpl implements DiscountService {

    private final UserCouponMapper userCouponMapper;

    private final CouponScopeService scopeService;

    private final Executor discountSolutionExecutor;

    @Override
    public List<CouponDiscountDTO> findDiscountSolution(List<OrderProductDTO> orderProducts) {
        // 1.查询我的所有可用优惠券
        List<Coupon> coupons = userCouponMapper.queryMyCoupons(UserContext.getUserId());
        if (PromotionUtils.isEmpty(coupons)) {
            return PromotionUtils.emptyList();
        }
        // 2.初筛
        // 2.1.计算订单总价
        int totalAmount = orderProducts.stream().mapToInt(OrderProductDTO::getPrice).sum();
        // 2.2.筛选可用券
        List<Coupon> availableCoupons = coupons.stream()
                .filter(c -> DiscountStrategy.getDiscount(c.getDiscountType()).canUse(totalAmount, c))
                .collect(Collectors.toList());
        if (PromotionUtils.isEmpty(availableCoupons)) {
            return PromotionUtils.emptyList();
        }
        // 3.排列组合出所有方案
        // 3.1.细筛（找出每一张优惠券的可用商品，判断商品总价是否达到券的使用门槛）
        Map<Coupon, List<OrderProductDTO>> availableCouponMap = findAvailableCoupon(availableCoupons, orderProducts);
        if (PromotionUtils.isEmpty(availableCouponMap.keySet())) {
            return PromotionUtils.emptyList();
        }
        // 3.2.排列组合
        availableCoupons = new ArrayList<>(availableCouponMap.keySet());
        List<List<Coupon>> solutions = PermuteUtil.permute(availableCoupons);
        // 3.3.添加单券的方案
        for (Coupon c : availableCoupons) {
            solutions.add(List.of(c));
        }
        // 4.计算方案的优惠明细
        List<CouponDiscountDTO> list = Collections.synchronizedList(new ArrayList<>(solutions.size()));
        // 4.1.定义闭锁
        CountDownLatch latch = new CountDownLatch(solutions.size());
        for (List<Coupon> solution : solutions) {
            // 4.2.异步计算
            CompletableFuture
                    .supplyAsync(
                            () -> calculateSolutionDiscount(availableCouponMap, orderProducts, solution),
                            discountSolutionExecutor
                    ).thenAccept(dto -> {
                        // 4.3.提交任务结果
                        list.add(dto);
                        latch.countDown();
                    });
        }
        // 4.4.等待运算结束
        try {
            latch.await(1, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            log.error("优惠方案计算被中断，{}", e.getMessage());
        }
        // 5.筛选最优解
        return findBestSolution(list);
    }

    @Override
    public CouponDiscountDTO queryDiscountDetailByOrder(OrderCouponDTO orderCouponDTO) {
        // 1.查询用户优惠券
        List<Long> userCouponIds = orderCouponDTO.getUserCouponIds();
        List<Coupon> coupons = userCouponMapper.queryCouponByUserCouponIds(userCouponIds, UserCouponStatus.UNUSED);
        if (PromotionUtils.isEmpty(coupons)) {
            return null;
        }
        // 2.查询优惠券对应可用商品
        Map<Coupon, List<OrderProductDTO>> availableCouponMap =
                findAvailableCoupon(coupons, orderCouponDTO.getProductList());
        if (PromotionUtils.isEmpty(availableCouponMap.keySet())) {
            return null;
        }
        // 3.查询优惠券规则并计算明细
        return calculateSolutionDiscount(availableCouponMap, orderCouponDTO.getProductList(), coupons);
    }

    private List<CouponDiscountDTO> findBestSolution(List<CouponDiscountDTO> list) {
        // 1.准备Map记录最优解
        Map<String, CouponDiscountDTO> moreDiscountMap = new HashMap<>();
        Map<Integer, CouponDiscountDTO> lessCouponMap = new HashMap<>();
        // 2.遍历，筛选最优解
        for (CouponDiscountDTO solution : list) {
            // 2.1.计算当前方案的id组合
            String ids = solution.getIds().stream()
                    .sorted(Long::compare).map(String::valueOf).collect(Collectors.joining(","));
            // 2.2.比较用券相同时，优惠金额是否最大
            CouponDiscountDTO best = moreDiscountMap.get(ids);
            if (best != null && best.getDiscountAmount() >= solution.getDiscountAmount()) {
                // 当前方案优惠金额少，跳过
                continue;
            }
            // 2.3.比较金额相同时，用券数量是否最少
            best = lessCouponMap.get(solution.getDiscountAmount());
            int size = solution.getIds().size();
            if (size > 1 && best != null && best.getIds().size() <= size) {
                // 当前方案用券更多，放弃
                continue;
            }
            // 2.4.更新最优解
            moreDiscountMap.put(ids, solution);
            lessCouponMap.put(solution.getDiscountAmount(), solution);
        }
        // 3.求交集（既满足优惠金额最大、又满足用券数量最少）
        Collection<CouponDiscountDTO> bestSolutions = PromotionUtils
                .intersection(moreDiscountMap.values(), lessCouponMap.values());
        // 4.排序，按优惠金额降序
        return bestSolutions.stream()
                .sorted(Comparator.comparingInt(CouponDiscountDTO::getDiscountAmount).reversed())
                .collect(Collectors.toList());
    }

    private CouponDiscountDTO calculateSolutionDiscount(
            Map<Coupon, List<OrderProductDTO>> couponMap, List<OrderProductDTO> products, List<Coupon> solution) {
        // 1.初始化DTO
        CouponDiscountDTO dto = new CouponDiscountDTO();
        // 2.初始化折扣明细的映射
        Map<Long, Integer> detailMap = products.stream()
                .collect(Collectors.toMap(OrderProductDTO::getId, p -> 0));
        dto.setDiscountDetail(detailMap);
        // 3.计算折扣
        for (Coupon coupon : solution) {
            // 3.1.获取优惠券限定范围对应的商品
            List<OrderProductDTO> availableProducts = couponMap.get(coupon);
            // 3.2.计算商品总价（商品原价 - 已计算折扣）
            int totalAmount = availableProducts.stream()
                    .mapToInt(p -> p.getPrice() - detailMap.get(p.getId())).sum();
            // 3.3.判断是否可用
            Discount discount = DiscountStrategy.getDiscount(coupon.getDiscountType());
            if (!discount.canUse(totalAmount, coupon)) {
                // 券不可用，跳过
                continue;
            }
            // 3.4.计算优惠金额
            int discountAmount = discount.calculateDiscount(totalAmount, coupon);
            // 3.5.计算优惠明细
            calculateDiscountDetails(detailMap, availableProducts, totalAmount, discountAmount);
            // 3.6.更新DTO数据
            dto.getIds().add(coupon.getUserCouponId());
            dto.getRules().add(discount.getRule(coupon));
            dto.setDiscountAmount(discountAmount + dto.getDiscountAmount());
        }
        return dto;
    }

    private void calculateDiscountDetails(Map<Long, Integer> detailMap, List<OrderProductDTO> products,
                                          int totalAmount, int discountAmount) {
        int times = 0;
        int remainDiscount = discountAmount;
        for (OrderProductDTO product : products) {
            // 更新商品已计算数量
            times++;
            int discount = 0;
            // 判断是否是最后一个商品
            if (times == products.size()) {
                // 是最后一个商品，总折扣金额 - 之前所有商品的折扣金额之和（消除按比例分摊的舍入误差）
                discount = remainDiscount;
            } else {
                // 计算折扣明细（商品价格在总价中占的比例，乘以总的折扣）
                discount = discountAmount * product.getPrice() / totalAmount;
                remainDiscount -= discount;
            }
            // 更新折扣明细
            detailMap.put(product.getId(), discount + detailMap.get(product.getId()));
        }
    }

    private Map<Coupon, List<OrderProductDTO>> findAvailableCoupon(
            List<Coupon> coupons, List<OrderProductDTO> products) {
        Map<Coupon, List<OrderProductDTO>> map = new HashMap<>(coupons.size());
        for (Coupon coupon : coupons) {
            // 1.找出优惠券的可用商品
            List<OrderProductDTO> availableProducts = products;
            if (Boolean.TRUE.equals(coupon.getSpecific())) {
                // 1.1.限定了范围，查询券的可用范围
                List<CouponScope> scopes = scopeService.lambdaQuery()
                        .eq(CouponScope::getCouponId, coupon.getId()).list();
                if (PromotionUtils.isEmpty(scopes)) {
                    continue;
                }
                // 1.2.按范围类型构建范围策略（分类/指定商品）
                ScopeType scopeType = ScopeType.of(scopes.get(0).getType());
                Set<Long> scopeIds = scopes.stream().map(CouponScope::getBizId).collect(Collectors.toSet());
                // 1.3.筛选商品
                availableProducts = products.stream()
                        .filter(p -> scopeType.buildScope(new ArrayList<>(scopeIds)).canUse(p))
                        .collect(Collectors.toList());
            }
            if (PromotionUtils.isEmpty(availableProducts)) {
                // 没有任何可用商品，抛弃
                continue;
            }
            // 2.计算范围商品总价
            int totalAmount = availableProducts.stream().mapToInt(OrderProductDTO::getPrice).sum();
            // 3.判断是否可用
            Discount discount = DiscountStrategy.getDiscount(coupon.getDiscountType());
            if (discount.canUse(totalAmount, coupon)) {
                map.put(coupon, availableProducts);
            }
        }
        return map;
    }
}
