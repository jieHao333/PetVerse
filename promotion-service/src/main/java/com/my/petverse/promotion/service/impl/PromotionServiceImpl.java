package com.my.petverse.promotion.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.entity.promotion.Promotion;
import com.my.petverse.promotion.mapper.PromotionMapper;
import com.my.petverse.promotion.service.PromotionService;
import org.springframework.stereotype.Service;

/**
 * 促销活动服务实现类（预留扩展）
 */
@Service
public class PromotionServiceImpl extends ServiceImpl<PromotionMapper, Promotion> implements PromotionService {
}
