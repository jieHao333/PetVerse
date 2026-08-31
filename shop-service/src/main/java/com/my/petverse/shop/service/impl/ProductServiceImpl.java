package com.my.petverse.shop.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.shop.ProductPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductSaveDTO;
import com.my.petverse.common.dto.shop.ProductUpdateDTO;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.enums.ProductCategory;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.ProductIndexMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.shop.ProductVO;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import com.my.petverse.shop.search.ProductSearchService;
import com.my.petverse.shop.service.MerchantService;
import com.my.petverse.shop.service.ProductService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 商品服务实现类
 */
@Service
@RequiredArgsConstructor
public class ProductServiceImpl extends ServiceImpl<ProductMapper, Product> implements ProductService {

    private final MerchantService merchantService;

    private final MerchantMapper merchantMapper;

    private final ProductSearchService productSearchService;

    private final MqEventPublisher mqEventPublisher;

    @Override
    public ProductVO saveProduct(Long userId, ProductSaveDTO dto) {
        Merchant merchant = merchantService.getActiveMerchant(userId);
        if (ProductCategory.of(dto.getCategory()) == null) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "商品类型不合法");
        }
        Product product = new Product();
        BeanUtils.copyProperties(dto, product);
        product.setMerchantId(merchant.getId());
        // 新增商品默认下架，商家确认信息后手动上架
        product.setStatus(0);
        save(product);
        // 事务提交后发事件，由索引消费者异步双写检索索引，失败由 RocketMQ 重试兼容，不影响商品保存
        mqEventPublisher.publishAfterCommit(MqTopics.PRODUCT_INDEX, MqTopics.TAG_PRODUCT_UPSERT,
                new ProductIndexMessage(product.getId(), merchant.getId()),
                "product-index:" + product.getId());
        return toVO(product, merchant.getShopName());
    }

    @Override
    public ProductVO updateProduct(Long userId, ProductUpdateDTO dto) {
        Merchant merchant = merchantService.getActiveMerchant(userId);
        Product product = getOwnProduct(merchant, dto.getId());
        // 仅更新传入的非空字段
        if (StringUtils.hasText(dto.getName())) {
            product.setName(dto.getName());
        }
        if (dto.getCategory() != null) {
            if (ProductCategory.of(dto.getCategory()) == null) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "商品类型不合法");
            }
            product.setCategory(dto.getCategory());
        }
        if (dto.getPrice() != null) {
            product.setPrice(dto.getPrice());
        }
        if (dto.getStock() != null) {
            product.setStock(dto.getStock());
        }
        if (StringUtils.hasText(dto.getImageUrl())) {
            product.setImageUrl(dto.getImageUrl());
        }
        if (dto.getDescription() != null) {
            product.setDescription(dto.getDescription());
        }
        if (dto.getStatus() != null) {
            product.setStatus(dto.getStatus());
        }
        updateById(product);
        // 事务提交后发事件，由索引消费者异步刷新检索索引（回表读取最新数据）
        mqEventPublisher.publishAfterCommit(MqTopics.PRODUCT_INDEX, MqTopics.TAG_PRODUCT_UPSERT,
                new ProductIndexMessage(product.getId(), merchant.getId()),
                "product-index:" + product.getId());
        return toVO(product, merchant.getShopName());
    }

    @Override
    public boolean deleteProduct(Long userId, Long productId) {
        Merchant merchant = merchantService.getActiveMerchant(userId);
        Product product = getOwnProduct(merchant, productId);
        boolean removed = removeById(product.getId());
        // 事务提交后发事件，由索引消费者异步删除检索索引（重复删除幂等）
        mqEventPublisher.publishAfterCommit(MqTopics.PRODUCT_INDEX, MqTopics.TAG_PRODUCT_REMOVE,
                new ProductIndexMessage(product.getId(), merchant.getId()),
                "product-index:" + product.getId());
        return removed;
    }

    @Override
    public PageResult<ProductVO> pageMine(Long userId, ProductPageQueryDTO dto) {
        Merchant merchant = merchantService.getActiveMerchant(userId);
        String keyword = resolveKeyword(dto);
        if (StringUtils.hasText(keyword)) {
            // 商家管理列表只搜自己店铺的商品，不限制上下架状态
            PageResult<Long> idPage = searchIdsQuietly(dto, keyword, null, merchant.getId());
            if (idPage != null) {
                List<ProductVO> vos = loadInOrder(idPage.getRecords(),
                        product -> toVO(product, merchant.getShopName()));
                return PageResult.of(idPage.getTotal(), dto.getPageNum(), dto.getPageSize(), vos);
            }
        }
        Page<Product> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<Product>()
                        .eq(Product::getMerchantId, merchant.getId())
                        .eq(dto.getCategory() != null, Product::getCategory, dto.getCategory())
                        .and(StringUtils.hasText(keyword), w -> w.like(Product::getName, keyword)
                                .or().like(Product::getDescription, keyword))
                        .orderByDesc(Product::getCreateTime));
        List<ProductVO> vos = page.getRecords().stream()
                .map(product -> toVO(product, merchant.getShopName()))
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    public PageResult<ProductVO> pageOnSale(ProductPageQueryDTO dto) {
        String keyword = resolveKeyword(dto);
        if (StringUtils.hasText(keyword)) {
            // 买家商城只搜上架商品
            PageResult<Long> idPage = searchIdsQuietly(dto, keyword, 1, dto.getMerchantId());
            if (idPage != null) {
                List<Product> products = loadInOrder(idPage.getRecords());
                Map<Long, String> shopNames = loadShopNames(products);
                List<ProductVO> vos = products.stream()
                        .map(product -> toVO(product, shopNames.get(product.getMerchantId())))
                        .collect(Collectors.toList());
                return PageResult.of(idPage.getTotal(), dto.getPageNum(), dto.getPageSize(), vos);
            }
        }
        Page<Product> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<Product>()
                        .eq(Product::getStatus, 1)
                        .eq(dto.getCategory() != null, Product::getCategory, dto.getCategory())
                        .eq(dto.getMerchantId() != null, Product::getMerchantId, dto.getMerchantId())
                        .and(StringUtils.hasText(keyword), w -> w.like(Product::getName, keyword)
                                .or().like(Product::getDescription, keyword))
                        .orderByDesc(Product::getCreateTime));
        Map<Long, String> shopNames = loadShopNames(page.getRecords());
        List<ProductVO> vos = page.getRecords().stream()
                .map(product -> toVO(product, shopNames.get(product.getMerchantId())))
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    /** keyword 为新的全文检索入口，name 保留兼容旧调用 */
    private String resolveKeyword(ProductPageQueryDTO dto) {
        return StringUtils.hasText(dto.getKeyword()) ? dto.getKeyword() : dto.getName();
    }

    /** ES 不可用或检索失败时返回 null，由调用方降级为数据库模糊查询 */
    private PageResult<Long> searchIdsQuietly(ProductPageQueryDTO dto, String keyword,
                                              Integer status, Long merchantId) {
        if (!productSearchService.isAvailable()) {
            return null;
        }
        return productSearchService.searchIds(dto, keyword, status, merchantId);
    }

    /** 按 ES 返回的顺序回表，保证相关度排序不被打乱 */
    private List<Product> loadInOrder(List<Long> ids) {
        if (ids.isEmpty()) {
            return List.of();
        }
        Map<Long, Product> productMap = listByIds(ids).stream()
                .collect(Collectors.toMap(Product::getId, Function.identity()));
        return ids.stream()
                .map(productMap::get)
                .filter(Objects::nonNull)
                .collect(Collectors.toList());
    }

    /** 按 ES 返回的顺序回表并转为 VO */
    private List<ProductVO> loadInOrder(List<Long> ids, Function<Product, ProductVO> converter) {
        return loadInOrder(ids).stream().map(converter).collect(Collectors.toList());
    }

    @Override
    public ProductVO getDetail(Long productId) {
        Product product = getById(productId);
        if (product == null || product.getStatus() == null || product.getStatus() != 1) {
            throw new BusinessException(ResultCode.NOT_FOUND, "商品不存在或已下架");
        }
        Merchant merchant = merchantMapper.selectById(product.getMerchantId());
        return toVO(product, merchant == null ? null : merchant.getShopName());
    }

    /** 查询商品并校验归属，防止越权操作他人店铺的商品 */
    private Product getOwnProduct(Merchant merchant, Long productId) {
        Product product = getById(productId);
        if (product == null || !Objects.equals(product.getMerchantId(), merchant.getId())) {
            throw new BusinessException(ResultCode.NOT_FOUND, "商品不存在");
        }
        return product;
    }

    /** 批量查询商品所属店铺名称 */
    private Map<Long, String> loadShopNames(List<Product> products) {
        Set<Long> merchantIds = products.stream()
                .map(Product::getMerchantId)
                .collect(Collectors.toSet());
        if (merchantIds.isEmpty()) {
            return Map.of();
        }
        return merchantMapper.selectBatchIds(merchantIds).stream()
                .collect(Collectors.toMap(Merchant::getId, Merchant::getShopName));
    }

    /** DO 转 VO，附加店铺名称与类型名称 */
    private ProductVO toVO(Product product, String shopName) {
        ProductVO vo = new ProductVO();
        BeanUtils.copyProperties(product, vo);
        vo.setShopName(shopName);
        ProductCategory category = ProductCategory.of(product.getCategory());
        vo.setCategoryName(category == null ? null : category.getDesc());
        return vo;
    }
}
