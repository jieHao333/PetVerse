package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.MerchantApply;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

/**
 * 商家入驻申请单数据访问接口
 */
@Mapper
public interface MerchantApplyMapper extends BaseMapper<MerchantApply> {

    /** 物理删除用户全部入驻申请（商家撤销冷静期结束后清除相关数据） */
    @Delete("DELETE FROM merchant_apply WHERE user_id = #{userId}")
    int physicalDeleteByUserId(@Param("userId") Long userId);
}
