package com.my.petverse.shop.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.shop.MerchantUpdateDTO;
import com.my.petverse.common.dto.shop.OrderPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductSaveDTO;
import com.my.petverse.common.dto.shop.ProductUpdateDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.MerchantVO;
import com.my.petverse.common.vo.shop.OrderVO;
import com.my.petverse.common.vo.shop.ProductVO;
import com.my.petverse.shop.service.MerchantService;
import com.my.petverse.shop.service.OrderService;
import com.my.petverse.shop.service.ProductService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 商家中心接口控制器（商家端，网关已校验 MERCHANT/ADMIN 角色）
 */
@RestController
@RequestMapping("/shop/merchant")
@RequiredArgsConstructor
public class MerchantController {

    private final MerchantService merchantService;

    private final ProductService productService;

    private final OrderService orderService;

    /** 查询我的店铺信息 */
    @GetMapping("/info")
    public Result<MerchantVO> info() {
        return Result.success(merchantService.getByUserId(UserContext.getUserId()));
    }

    /** 修改我的店铺信息（名称/LOGO/简介/联系电话） */
    @PutMapping("/info")
    public Result<MerchantVO> updateInfo(@RequestBody @Valid MerchantUpdateDTO dto) {
        return Result.success(merchantService.updateInfo(UserContext.getUserId(), dto));
    }

    /** 新增商品（默认下架，编辑完成后手动上架） */
    @PostMapping("/product")
    public Result<ProductVO> saveProduct(@RequestBody @Valid ProductSaveDTO dto) {
        return Result.success(productService.saveProduct(UserContext.getUserId(), dto));
    }

    /** 修改商品（含上下架） */
    @PutMapping("/product")
    public Result<ProductVO> updateProduct(@RequestBody @Valid ProductUpdateDTO dto) {
        return Result.success(productService.updateProduct(UserContext.getUserId(), dto));
    }

    /** 删除商品（逻辑删除） */
    @DeleteMapping("/product/{id}")
    public Result<Boolean> deleteProduct(@PathVariable("id") Long id) {
        return Result.success(productService.deleteProduct(UserContext.getUserId(), id));
    }

    /** 分页查询我的商品（含下架商品） */
    @GetMapping("/product/page")
    public Result<PageResult<ProductVO>> pageMine(@Valid ProductPageQueryDTO dto) {
        return Result.success(productService.pageMine(UserContext.getUserId(), dto));
    }

    /** 分页查询店铺订单（支持状态筛选） */
    @GetMapping("/order/page")
    public Result<PageResult<OrderVO>> pageOrders(@Valid OrderPageQueryDTO dto) {
        return Result.success(orderService.pageMerchant(UserContext.getUserId(), dto));
    }

    /** 到店核销完成订单（买家凭取货码取货） */
    @PostMapping("/order/{id}/complete")
    public Result<OrderVO> completeOrder(@PathVariable("id") Long id,
                                         @RequestParam(value = "pickupCode", required = false) String pickupCode) {
        return Result.success(orderService.completeOrder(UserContext.getUserId(), id, pickupCode));
    }
}
