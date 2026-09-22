package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.CartItem;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

import java.util.Collection;

/**
 * 购物车条目 Mapper
 */
@Mapper
public interface CartItemMapper extends BaseMapper<CartItem> {

    /**
     * 数量累加：一条 UPDATE 完成「读取现值 + 累加 + 上限校验」，
     * 累加后会超过 maxQuantity 时不做修改，返回 0 表示行不存在或已到上限
     */
    @Update("UPDATE cart_item SET quantity = quantity + #{delta}, update_time = NOW() "
            + "WHERE user_id = #{userId} AND product_id = #{productId} AND deleted = 0 "
            + "AND quantity + #{delta} <= #{maxQuantity}")
    int accumulateQuantity(@Param("userId") Long userId,
                           @Param("productId") Long productId,
                           @Param("delta") int delta,
                           @Param("maxQuantity") int maxQuantity);

    /** 物理删除单个条目（已删条目不再占用 (user_id, product_id) 唯一索引） */
    @Delete("DELETE FROM cart_item WHERE id = #{id} AND user_id = #{userId}")
    int physicalDelete(@Param("id") Long id, @Param("userId") Long userId);

    /** 批量物理删除条目（下单成功后清空已结算条目） */
    @Delete("<script>DELETE FROM cart_item WHERE user_id = #{userId} AND id IN "
            + "<foreach collection='ids' item='id' open='(' separator=',' close=')'>#{id}</foreach></script>")
    int physicalDeleteBatch(@Param("userId") Long userId, @Param("ids") Collection<Long> ids);
}