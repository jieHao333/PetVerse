package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.Merchant;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

/**
 * 商家店铺数据访问接口
 */
@Mapper
public interface MerchantMapper extends BaseMapper<Merchant> {

    /**
     * 物理删除店铺：撤销冷静期结束后彻底清除商家数据。
     * uk_user_id 唯一索引包含已逻辑删除行，必须物理删除才能让同一用户重新入驻
     */
    @Delete("DELETE FROM merchant WHERE id = #{id}")
    int physicalDeleteById(@Param("id") Long id);
}
