package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.CartItem;
import org.apache.ibatis.annotations.Mapper;

/**
 * 购物车条目 Mapper
 */
@Mapper
public interface CartItemMapper extends BaseMapper<CartItem> {
}
