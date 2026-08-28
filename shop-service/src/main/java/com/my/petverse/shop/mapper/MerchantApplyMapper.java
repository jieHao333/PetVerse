package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.MerchantApply;
import org.apache.ibatis.annotations.Mapper;

/**
 * 商家入驻申请单数据访问接口
 */
@Mapper
public interface MerchantApplyMapper extends BaseMapper<MerchantApply> {
}
