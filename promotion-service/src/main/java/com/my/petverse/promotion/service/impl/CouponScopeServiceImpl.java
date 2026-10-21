package com.my.petverse.promotion.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.entity.promotion.CouponScope;
import com.my.petverse.promotion.mapper.CouponScopeMapper;
import com.my.petverse.promotion.service.CouponScopeService;
import org.springframework.stereotype.Service;

/**
 * 优惠券作用范围服务实现类
 */
@Service
public class CouponScopeServiceImpl extends ServiceImpl<CouponScopeMapper, CouponScope> implements CouponScopeService {
}
