package com.my.petverse.shop.controller;

import com.my.petverse.common.dto.promotion.ProductSimpleInfoDTO;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.result.Result;
import com.my.petverse.shop.mapper.ProductMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 商品内部接口控制器（服务间调用，不经网关对外暴露）：
 * 供 promotion-service 展示优惠券限定商品范围时批量查询商品简要信息
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/shop/product/internal")
public class InternalProductController {

    private final ProductMapper productMapper;

    /** 批量查询商品简要信息 */
    @GetMapping("/batch")
    public Result<List<ProductSimpleInfoDTO>> listSimpleInfo(@RequestParam("ids") List<Long> ids) {
        if (ids == null || ids.isEmpty()) {
            return Result.success(List.of());
        }
        List<ProductSimpleInfoDTO> list = productMapper.selectBatchIds(ids).stream()
                .map(this::toSimpleInfo)
                .toList();
        return Result.success(list);
    }

    private ProductSimpleInfoDTO toSimpleInfo(Product product) {
        ProductSimpleInfoDTO dto = new ProductSimpleInfoDTO();
        dto.setId(product.getId());
        dto.setName(product.getName());
        dto.setCategory(product.getCategory());
        return dto;
    }
}
