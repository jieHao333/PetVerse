package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.Product;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

/**
 * 商品数据访问接口
 */
@Mapper
public interface ProductMapper extends BaseMapper<Product> {

    /** 物理删除商家名下全部商品（撤销冷静期结束后清除相关数据） */
    @Delete("DELETE FROM product WHERE merchant_id = #{merchantId}")
    int physicalDeleteByMerchantId(@Param("merchantId") Long merchantId);
}
