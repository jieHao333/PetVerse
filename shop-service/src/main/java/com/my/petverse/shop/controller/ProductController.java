package com.my.petverse.shop.controller;

import com.my.petverse.common.dto.shop.ProductPageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.ProductVO;
import com.my.petverse.shop.service.ProductService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 商城商品浏览接口控制器（买家端，所有登录用户可访问）
 */
@RestController
@RequestMapping("/shop/product")
@RequiredArgsConstructor
public class ProductController {

    private final ProductService productService;

    /** 分页浏览商城商品（仅上架商品，支持名称/类型/店铺筛选） */
    @GetMapping("/page")
    public Result<PageResult<ProductVO>> page(@Valid ProductPageQueryDTO dto) {
        return Result.success(productService.pageOnSale(dto));
    }

    /** 查询商品详情（仅上架商品可见） */
    @GetMapping("/{id}")
    public Result<ProductVO> detail(@PathVariable("id") Long id) {
        return Result.success(productService.getDetail(id));
    }
}
