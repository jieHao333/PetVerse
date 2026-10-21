package com.my.petverse.promotion.strategy.scope;

import com.my.petverse.common.dto.promotion.ProductSimpleInfoDTO;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.promotion.constants.ScopeType;
import com.my.petverse.promotion.feign.ShopProductClient;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.stream.Collectors;

/**
 * 指定商品范围名称处理器：Feign 调用 shop-service 内部接口批量查询商品名称
 */
@RequiredArgsConstructor
@Component(ScopeType.PRODUCT_HANDLER_NAME)
public class ProductScopeNameHandler implements ScopeNameHandler {

    private final ShopProductClient shopProductClient;

    @Override
    public List<String> getNameByIds(List<Long> scopeIds) {
        Result<List<ProductSimpleInfoDTO>> result = shopProductClient.listSimpleInfo(scopeIds);
        List<ProductSimpleInfoDTO> infos = result == null ? null : result.getData();
        if (infos == null || infos.isEmpty()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "商品信息不存在");
        }
        return infos.stream().map(ProductSimpleInfoDTO::getName).collect(Collectors.toList());
    }
}
