package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.ShopOrderItem;
import org.apache.ibatis.annotations.Mapper;

/**
 * 订单明细 Mapper
 */
@Mapper
public interface ShopOrderItemMapper extends BaseMapper<ShopOrderItem> {
}
