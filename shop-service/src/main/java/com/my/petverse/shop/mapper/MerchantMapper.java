package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.Merchant;
import org.apache.ibatis.annotations.Mapper;

/**
 * 商家店铺数据访问接口
 */
@Mapper
public interface MerchantMapper extends BaseMapper<Merchant> {
}
