package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.Product;
import org.apache.ibatis.annotations.Mapper;

/**
 * 商品数据访问接口
 */
@Mapper
public interface ProductMapper extends BaseMapper<Product> {
}
