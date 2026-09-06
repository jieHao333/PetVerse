package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.ProductReview;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

/**
 * 商品评价数据访问接口
 */
@Mapper
public interface ProductReviewMapper extends BaseMapper<ProductReview> {

    /**
     * 统计用户对某商品的历史评价数（含已逻辑删除的记录）
     * 逻辑删除后唯一键 uk_product_user 仍占用该槽位，用于判定"删除后无法重新评价"
     */
    @Select("SELECT COUNT(*) FROM product_review WHERE product_id = #{productId} AND user_id = #{userId}")
    long countIncludingDeleted(@Param("productId") Long productId, @Param("userId") Long userId);
}
