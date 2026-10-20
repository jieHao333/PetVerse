package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.ProductReview;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

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

    /** 查询商家名下全部评价ID（含已逻辑删除，用于级联清理评价回复） */
    @Select("SELECT id FROM product_review WHERE merchant_id = #{merchantId}")
    List<Long> selectIdsByMerchantId(@Param("merchantId") Long merchantId);

    /** 物理删除商家名下全部评价（撤销冷静期结束后清除相关数据） */
    @Delete("DELETE FROM product_review WHERE merchant_id = #{merchantId}")
    int physicalDeleteByMerchantId(@Param("merchantId") Long merchantId);
}
