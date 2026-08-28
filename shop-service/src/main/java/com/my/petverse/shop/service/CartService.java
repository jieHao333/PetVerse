package com.my.petverse.shop.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.shop.CartAddDTO;
import com.my.petverse.common.dto.shop.CartUpdateDTO;
import com.my.petverse.common.entity.shop.CartItem;
import com.my.petverse.common.vo.shop.CartItemVO;

import java.util.List;

/**
 * 购物车服务接口
 */
public interface CartService extends IService<CartItem> {

    /**
     * 加入购物车（已存在同一商品则累加数量）
     *
     * @param userId 买家用户ID
     * @param dto    加购参数
     * @return 购物车条目
     */
    CartItemVO addItem(Long userId, CartAddDTO dto);

    /**
     * 查询我的购物车列表（附商品与店铺信息，前端按店铺分组）
     *
     * @param userId 买家用户ID
     * @return 购物车条目列表
     */
    List<CartItemVO> listMine(Long userId);

    /**
     * 修改购物车条目数量
     *
     * @param userId 买家用户ID
     * @param dto    修改参数
     * @return 是否成功
     */
    boolean updateQuantity(Long userId, CartUpdateDTO dto);

    /**
     * 删除购物车条目
     *
     * @param userId 买家用户ID
     * @param itemId 购物车条目ID
     * @return 是否成功
     */
    boolean removeItem(Long userId, Long itemId);
}
