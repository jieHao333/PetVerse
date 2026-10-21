package com.my.petverse.promotion.feign;

import com.my.petverse.common.dto.promotion.ProductSimpleInfoDTO;
import com.my.petverse.common.result.Result;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.List;

/**
 * 商城服务 Feign 客户端：优惠券限定商品范围时批量查询商品名称
 */
@FeignClient(name = "shop-service")
public interface ShopProductClient {

    /** 批量查询商品简要信息（内部接口） */
    @GetMapping("/shop/product/internal/batch")
    Result<List<ProductSimpleInfoDTO>> listSimpleInfo(@RequestParam("ids") List<Long> ids);
}
