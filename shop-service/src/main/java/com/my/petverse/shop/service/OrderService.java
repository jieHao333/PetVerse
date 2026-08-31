package com.my.petverse.shop.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.shop.OrderCreateDTO;
import com.my.petverse.common.dto.shop.OrderPageQueryDTO;
import com.my.petverse.common.entity.shop.ShopOrder;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.shop.OrderVO;

/**
 * 订单服务接口（目前仅支持到店自取，预留外卖配送扩展）
 */
public interface OrderService extends IService<ShopOrder> {

    /**
     * 从购物车勾选商品创建订单（同一店铺，扣减库存，生成待支付订单）
     *
     * @param userId 买家用户ID
     * @param dto    下单参数
     * @return 订单信息（待支付）
     */
    OrderVO createOrder(Long userId, OrderCreateDTO dto);

    /**
     * 支付订单（模拟支付，成功后生成取货码，订单进入待取货状态）
     *
     * @param userId  买家用户ID
     * @param orderId 订单ID
     * @return 支付后的订单信息
     */
    OrderVO payOrder(Long userId, Long orderId);

    /**
     * 取消订单（仅待支付订单可取消，回补库存）
     *
     * @param userId  买家用户ID
     * @param orderId 订单ID
     * @return 是否成功
     */
    boolean cancelOrder(Long userId, Long orderId);

    /**
     * 超时自动取消（由延迟消息消费者调用）：仅待支付订单会被取消并回补库存，
     * 已支付/已取消/已核销订单一律跳过，天然幂等，可安全重复消费
     *
     * @param orderId 订单ID
     * @return 是否执行了取消（非待支付状态返回 false）
     */
    boolean timeoutCancelOrder(Long orderId);

    /**
     * 分页查询我的订单（买家端）
     *
     * @param userId 买家用户ID
     * @param dto    查询参数
     * @return 订单分页
     */
    PageResult<OrderVO> pageMine(Long userId, OrderPageQueryDTO dto);

    /**
     * 查询订单详情（仅买家本人可见）
     *
     * @param userId  买家用户ID
     * @param orderId 订单ID
     * @return 订单信息
     */
    OrderVO getMyOrder(Long userId, Long orderId);

    /**
     * 分页查询店铺订单（商家端）
     *
     * @param userId 店主用户ID
     * @param dto    查询参数
     * @return 订单分页
     */
    PageResult<OrderVO> pageMerchant(Long userId, OrderPageQueryDTO dto);

    /**
     * 到店核销完成订单（商家端，买家凭取货码到店取货）
     *
     * @param userId     店主用户ID
     * @param orderId    订单ID
     * @param pickupCode 买家出示的取货码
     * @return 核销后的订单信息
     */
    OrderVO completeOrder(Long userId, Long orderId, String pickupCode);
}
