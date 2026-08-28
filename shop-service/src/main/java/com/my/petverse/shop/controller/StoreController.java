package com.my.petverse.shop.controller;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.MerchantVO;
import com.my.petverse.shop.service.MerchantService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 店家页面接口控制器（买家端，所有登录用户可访问）
 * 注意：不能放在 /shop/merchant 前缀下，该前缀被网关限制为商家/管理员角色
 */
@RestController
@RequestMapping("/shop/store")
@RequiredArgsConstructor
public class StoreController {

    private final MerchantService merchantService;

    /** 查询店铺公开信息（店家页面头部展示，仅营业中店铺可见） */
    @GetMapping("/{id}")
    public Result<MerchantVO> info(@PathVariable("id") Long id) {
        return Result.success(merchantService.getShopById(id));
    }
}
