package com.my.petverse.shop.controller;

import com.my.petverse.common.dto.shop.OrderCreateDTO;
import com.my.petverse.common.dto.shop.OrderPageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.OrderVO;
import com.my.petverse.shop.service.OrderService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 订单接口控制器（买家端，所有登录用户可访问）
 * 流程：购物车结算下单（待支付）→ 模拟支付（生成取货码）→ 到店取货核销完成
 */
@RestController
@RequestMapping("/shop/order")
@RequiredArgsConstructor
public class OrderController {

    private final OrderService orderService;

    /** 从购物车勾选商品创建订单（同一店铺，扣库存，待支付） */
    @PostMapping
    public Result<OrderVO> create(@RequestHeader("X-User-Id") Long userId,
                                  @RequestBody @Valid OrderCreateDTO dto) {
        return Result.success(orderService.createOrder(userId, dto));
    }

    /** 支付订单（模拟支付，成功后生成取货码） */
    @PostMapping("/{id}/pay")
    public Result<OrderVO> pay(@RequestHeader("X-User-Id") Long userId,
                               @PathVariable("id") Long id) {
        return Result.success(orderService.payOrder(userId, id));
    }

    /** 取消订单（仅待支付可取消，回补库存） */
    @PostMapping("/{id}/cancel")
    public Result<Boolean> cancel(@RequestHeader("X-User-Id") Long userId,
                                  @PathVariable("id") Long id) {
        return Result.success(orderService.cancelOrder(userId, id));
    }

    /** 分页查询我的订单（支持状态筛选） */
    @GetMapping("/page")
    public Result<PageResult<OrderVO>> page(@RequestHeader("X-User-Id") Long userId,
                                            @Valid OrderPageQueryDTO dto) {
        return Result.success(orderService.pageMine(userId, dto));
    }

    /** 查询订单详情（仅本人可见） */
    @GetMapping("/{id}")
    public Result<OrderVO> detail(@RequestHeader("X-User-Id") Long userId,
                                  @PathVariable("id") Long id) {
        return Result.success(orderService.getMyOrder(userId, id));
    }
}
