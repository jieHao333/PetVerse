package com.my.petverse.shop.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.shop.CartAddDTO;
import com.my.petverse.common.dto.shop.CartUpdateDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.CartItemVO;
import com.my.petverse.shop.service.CartService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 购物车接口控制器（买家端，所有登录用户可访问）
 */
@RestController
@RequestMapping("/shop/cart")
@RequiredArgsConstructor
public class CartController {

    private final CartService cartService;

    /** 加入购物车（同一商品重复加购累加数量） */
    @PostMapping
    public Result<CartItemVO> add(@RequestBody @Valid CartAddDTO dto) {
        return Result.success(cartService.addItem(UserContext.getUserId(), dto));
    }

    /** 查询我的购物车列表（前端按店铺分组展示） */
    @GetMapping
    public Result<List<CartItemVO>> list() {
        return Result.success(cartService.listMine(UserContext.getUserId()));
    }

    /** 修改购物车条目数量 */
    @PutMapping
    public Result<Boolean> update(@RequestBody @Valid CartUpdateDTO dto) {
        return Result.success(cartService.updateQuantity(UserContext.getUserId(), dto));
    }

    /** 删除购物车条目 */
    @DeleteMapping("/{id}")
    public Result<Boolean> remove(@PathVariable("id") Long id) {
        return Result.success(cartService.removeItem(UserContext.getUserId(), id));
    }
}
