package com.my.petverse.shop.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.shop.CartAddDTO;
import com.my.petverse.common.dto.shop.CartUpdateDTO;
import com.my.petverse.common.entity.shop.CartItem;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.shop.CartItemVO;
import com.my.petverse.shop.mapper.CartItemMapper;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import com.my.petverse.shop.service.CartService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 购物车服务实现类
 */
@Service
@RequiredArgsConstructor
public class CartServiceImpl extends ServiceImpl<CartItemMapper, CartItem> implements CartService {

    private final ProductMapper productMapper;

    private final MerchantMapper merchantMapper;

    /** 单个商品加购数量上限 */
    private static final int MAX_QUANTITY = 999;

    @Override
    public CartItemVO addItem(Long userId, CartAddDTO dto) {
        Product product = productMapper.selectById(dto.getProductId());
        if (product == null || product.getStatus() == null || product.getStatus() != 1) {
            throw new BusinessException(ResultCode.NOT_FOUND, "商品不存在或已下架");
        }
        // 已加购同一商品则累加数量，避免购物车出现重复条目
        CartItem item = getOne(new LambdaQueryWrapper<CartItem>()
                .eq(CartItem::getUserId, userId)
                .eq(CartItem::getProductId, dto.getProductId()));
        int quantity = dto.getQuantity() + (item == null ? 0 : item.getQuantity());
        if (quantity > MAX_QUANTITY) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "单个商品加购数量不能超过" + MAX_QUANTITY);
        }
        if (product.getStock() != null && quantity > product.getStock()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "加购数量超过库存，当前库存 " + product.getStock());
        }
        if (item == null) {
            item = new CartItem();
            item.setUserId(userId);
            item.setProductId(dto.getProductId());
            item.setQuantity(quantity);
            save(item);
        } else {
            item.setQuantity(quantity);
            updateById(item);
        }
        Merchant merchant = merchantMapper.selectById(product.getMerchantId());
        return toVO(item, product, merchant == null ? null : merchant.getShopName());
    }

    @Override
    public List<CartItemVO> listMine(Long userId) {
        List<CartItem> items = list(new LambdaQueryWrapper<CartItem>()
                .eq(CartItem::getUserId, userId)
                .orderByDesc(CartItem::getCreateTime));
        if (items.isEmpty()) {
            return List.of();
        }
        // 批量查询商品与店铺，避免循环查库
        Set<Long> productIds = items.stream().map(CartItem::getProductId).collect(Collectors.toSet());
        Map<Long, Product> products = productMapper.selectBatchIds(productIds).stream()
                .collect(Collectors.toMap(Product::getId, Function.identity()));
        Set<Long> merchantIds = products.values().stream()
                .map(Product::getMerchantId)
                .collect(Collectors.toSet());
        Map<Long, String> shopNames = merchantIds.isEmpty() ? Map.of()
                : merchantMapper.selectBatchIds(merchantIds).stream()
                        .collect(Collectors.toMap(Merchant::getId, Merchant::getShopName));
        return items.stream()
                .map(item -> {
                    Product product = products.get(item.getProductId());
                    String shopName = product == null ? null : shopNames.get(product.getMerchantId());
                    return toVO(item, product, shopName);
                })
                .collect(Collectors.toList());
    }

    @Override
    public boolean updateQuantity(Long userId, CartUpdateDTO dto) {
        CartItem item = getOwnItem(userId, dto.getId());
        Product product = productMapper.selectById(item.getProductId());
        if (product == null || product.getStatus() == null || product.getStatus() != 1) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "商品已失效，请删除后重新选购");
        }
        if (product.getStock() != null && dto.getQuantity() > product.getStock()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "购买数量超过库存，当前库存 " + product.getStock());
        }
        item.setQuantity(dto.getQuantity());
        return updateById(item);
    }

    @Override
    public boolean removeItem(Long userId, Long itemId) {
        CartItem item = getOwnItem(userId, itemId);
        return removeById(item.getId());
    }

    /** 查询购物车条目并校验归属，防止越权操作他人购物车 */
    private CartItem getOwnItem(Long userId, Long itemId) {
        CartItem item = getById(itemId);
        if (item == null || !Objects.equals(item.getUserId(), userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "购物车条目不存在");
        }
        return item;
    }

    /** DO 转 VO，附加商品与店铺信息（商品被删除/下架时标记失效） */
    private CartItemVO toVO(CartItem item, Product product, String shopName) {
        CartItemVO vo = new CartItemVO();
        vo.setId(item.getId());
        vo.setProductId(item.getProductId());
        vo.setQuantity(item.getQuantity());
        if (product != null) {
            vo.setMerchantId(product.getMerchantId());
            vo.setShopName(shopName);
            vo.setProductName(product.getName());
            vo.setProductImage(product.getImageUrl());
            vo.setPrice(product.getPrice());
            vo.setStock(product.getStock());
            vo.setValid(product.getStatus() != null && product.getStatus() == 1);
        } else {
            vo.setValid(false);
        }
        return vo;
    }
}
