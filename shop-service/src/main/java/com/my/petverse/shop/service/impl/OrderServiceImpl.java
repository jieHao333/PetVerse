package com.my.petverse.shop.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.shop.OrderCreateDTO;
import com.my.petverse.common.dto.shop.OrderPageQueryDTO;
import com.my.petverse.common.entity.shop.CartItem;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.entity.shop.ShopOrder;
import com.my.petverse.common.entity.shop.ShopOrderItem;
import com.my.petverse.common.enums.OrderStatus;
import com.my.petverse.common.enums.PickupType;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.shop.OrderItemVO;
import com.my.petverse.common.vo.shop.OrderVO;
import com.my.petverse.shop.mapper.CartItemMapper;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import com.my.petverse.shop.mapper.ShopOrderItemMapper;
import com.my.petverse.shop.mapper.ShopOrderMapper;
import com.my.petverse.shop.service.MerchantService;
import com.my.petverse.shop.service.OrderService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.math.BigDecimal;
import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 订单服务实现类（目前仅支持到店自取，预留外卖配送扩展）
 */
@Service
@RequiredArgsConstructor
public class OrderServiceImpl extends ServiceImpl<ShopOrderMapper, ShopOrder> implements OrderService {

    private final CartItemMapper cartItemMapper;

    private final ProductMapper productMapper;

    private final MerchantMapper merchantMapper;

    private final ShopOrderItemMapper orderItemMapper;

    private final MerchantService merchantService;

    /** 订单号时间前缀格式 */
    private static final DateTimeFormatter ORDER_NO_FORMAT = DateTimeFormatter.ofPattern("yyyyMMddHHmmss");

    private static final SecureRandom RANDOM = new SecureRandom();

    @Override
    @Transactional(rollbackFor = Exception.class)
    public OrderVO createOrder(Long userId, OrderCreateDTO dto) {
        // 校验购物车条目归属，防止越权结算他人购物车
        List<CartItem> cartItems = cartItemMapper.selectBatchIds(dto.getCartItemIds()).stream()
                .filter(item -> Objects.equals(item.getUserId(), userId))
                .collect(Collectors.toList());
        if (cartItems.size() != dto.getCartItemIds().size()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "部分购物车条目不存在，请刷新后重试");
        }
        // 校验商品有效性
        Set<Long> productIds = cartItems.stream().map(CartItem::getProductId).collect(Collectors.toSet());
        Map<Long, Product> products = productMapper.selectBatchIds(productIds).stream()
                .collect(Collectors.toMap(Product::getId, p -> p));
        for (CartItem item : cartItems) {
            Product product = products.get(item.getProductId());
            if (product == null || product.getStatus() == null || product.getStatus() != 1) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "商品已下架或不存在，请移除后重新结算");
            }
        }
        // 到店自取需到对应店铺取货，一次只能结算同一店铺的商品
        Set<Long> merchantIds = products.values().stream()
                .map(Product::getMerchantId)
                .collect(Collectors.toSet());
        if (merchantIds.size() > 1) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "一次只能结算同一店铺的商品，请分开下单");
        }
        Long merchantId = merchantIds.iterator().next();
        Merchant merchant = merchantMapper.selectById(merchantId);
        if (merchant == null || merchant.getStatus() == null || merchant.getStatus() != 1) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "店铺已停止营业，无法下单");
        }
        // 原子扣减库存（stock >= quantity 才更新），并发下防止超卖
        for (CartItem item : cartItems) {
            Product product = products.get(item.getProductId());
            int updated = productMapper.update(null, new LambdaUpdateWrapper<Product>()
                    .eq(Product::getId, product.getId())
                    .ge(Product::getStock, item.getQuantity())
                    .setSql("stock = stock - {0}", item.getQuantity()));
            if (updated == 0) {
                throw new BusinessException(ResultCode.BAD_REQUEST,
                        "商品「" + product.getName() + "」库存不足，请调整数量");
            }
        }
        // 生成待支付订单与商品快照明细
        ShopOrder order = new ShopOrder();
        order.setOrderNo(generateOrderNo());
        order.setUserId(userId);
        order.setMerchantId(merchantId);
        order.setStatus(OrderStatus.PENDING_PAYMENT.getCode());
        order.setPickupType(PickupType.SELF_PICKUP.getCode());
        order.setRemark(dto.getRemark());
        BigDecimal totalAmount = BigDecimal.ZERO;
        for (CartItem item : cartItems) {
            Product product = products.get(item.getProductId());
            totalAmount = totalAmount.add(product.getPrice().multiply(BigDecimal.valueOf(item.getQuantity())));
        }
        order.setTotalAmount(totalAmount);
        save(order);
        for (CartItem item : cartItems) {
            Product product = products.get(item.getProductId());
            ShopOrderItem orderItem = new ShopOrderItem();
            orderItem.setOrderId(order.getId());
            orderItem.setProductId(product.getId());
            orderItem.setProductName(product.getName());
            orderItem.setProductImage(product.getImageUrl());
            orderItem.setPrice(product.getPrice());
            orderItem.setQuantity(item.getQuantity());
            orderItem.setAmount(product.getPrice().multiply(BigDecimal.valueOf(item.getQuantity())));
            orderItemMapper.insert(orderItem);
        }
        // 已结算条目移出购物车
        cartItemMapper.deleteBatchIds(dto.getCartItemIds());
        return toVO(order, merchant.getShopName(), listItems(order.getId()));
    }

    @Override
    public OrderVO payOrder(Long userId, Long orderId) {
        ShopOrder order = getOwnOrder(userId, orderId);
        if (!Objects.equals(order.getStatus(), OrderStatus.PENDING_PAYMENT.getCode())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "订单不是待支付状态，无法支付");
        }
        // 模拟支付成功，生成取货码作为到店核销凭证
        order.setStatus(OrderStatus.PENDING_PICKUP.getCode());
        order.setPickupCode(generatePickupCode());
        order.setPayTime(LocalDateTime.now());
        updateById(order);
        return toVO(order, loadShopName(order.getMerchantId()), listItems(order.getId()));
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean cancelOrder(Long userId, Long orderId) {
        ShopOrder order = getOwnOrder(userId, orderId);
        if (!Objects.equals(order.getStatus(), OrderStatus.PENDING_PAYMENT.getCode())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "仅待支付订单可以取消");
        }
        // 回补扣减的库存
        for (ShopOrderItem item : listOrderItems(orderId)) {
            productMapper.update(null, new LambdaUpdateWrapper<Product>()
                    .eq(Product::getId, item.getProductId())
                    .setSql("stock = stock + {0}", item.getQuantity()));
        }
        order.setStatus(OrderStatus.CANCELLED.getCode());
        order.setCancelTime(LocalDateTime.now());
        return updateById(order);
    }

    @Override
    public PageResult<OrderVO> pageMine(Long userId, OrderPageQueryDTO dto) {
        Page<ShopOrder> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<ShopOrder>()
                        .eq(ShopOrder::getUserId, userId)
                        .eq(dto.getStatus() != null, ShopOrder::getStatus, dto.getStatus())
                        .orderByDesc(ShopOrder::getCreateTime));
        return toPageResult(page);
    }

    @Override
    public OrderVO getMyOrder(Long userId, Long orderId) {
        ShopOrder order = getOwnOrder(userId, orderId);
        return toVO(order, loadShopName(order.getMerchantId()), listItems(order.getId()));
    }

    @Override
    public PageResult<OrderVO> pageMerchant(Long userId, OrderPageQueryDTO dto) {
        Merchant merchant = merchantService.getActiveMerchant(userId);
        Page<ShopOrder> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<ShopOrder>()
                        .eq(ShopOrder::getMerchantId, merchant.getId())
                        .eq(dto.getStatus() != null, ShopOrder::getStatus, dto.getStatus())
                        .orderByDesc(ShopOrder::getCreateTime));
        return toPageResult(page);
    }

    @Override
    public OrderVO completeOrder(Long userId, Long orderId, String pickupCode) {
        Merchant merchant = merchantService.getActiveMerchant(userId);
        ShopOrder order = getById(orderId);
        // 校验订单归属本店，防止越权核销他店订单
        if (order == null || !Objects.equals(order.getMerchantId(), merchant.getId())) {
            throw new BusinessException(ResultCode.NOT_FOUND, "订单不存在");
        }
        if (!Objects.equals(order.getStatus(), OrderStatus.PENDING_PICKUP.getCode())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "订单不是待取货状态，无法核销");
        }
        // 核对买家出示的取货码
        if (StringUtils.hasText(pickupCode) && !pickupCode.trim().equals(order.getPickupCode())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "取货码不正确");
        }
        order.setStatus(OrderStatus.COMPLETED.getCode());
        order.setFinishTime(LocalDateTime.now());
        updateById(order);
        return toVO(order, merchant.getShopName(), listItems(order.getId()));
    }

    /** 查询订单并校验归属，防止越权访问他人订单 */
    private ShopOrder getOwnOrder(Long userId, Long orderId) {
        ShopOrder order = getById(orderId);
        if (order == null || !Objects.equals(order.getUserId(), userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "订单不存在");
        }
        return order;
    }

    /** 分页结果统一转换：批量加载明细与店铺名，避免循环查库 */
    private PageResult<OrderVO> toPageResult(Page<ShopOrder> page) {
        List<ShopOrder> orders = page.getRecords();
        if (orders.isEmpty()) {
            return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), List.of());
        }
        Set<Long> orderIds = orders.stream().map(ShopOrder::getId).collect(Collectors.toSet());
        Map<Long, List<OrderItemVO>> itemsMap = orderItemMapper.selectList(
                        new LambdaQueryWrapper<ShopOrderItem>().in(ShopOrderItem::getOrderId, orderIds)).stream()
                .collect(Collectors.groupingBy(ShopOrderItem::getOrderId,
                        Collectors.mapping(this::toItemVO, Collectors.toList())));
        Set<Long> merchantIds = orders.stream().map(ShopOrder::getMerchantId).collect(Collectors.toSet());
        Map<Long, String> shopNames = merchantMapper.selectBatchIds(merchantIds).stream()
                .collect(Collectors.toMap(Merchant::getId, Merchant::getShopName));
        List<OrderVO> vos = orders.stream()
                .map(order -> toVO(order, shopNames.get(order.getMerchantId()),
                        itemsMap.getOrDefault(order.getId(), List.of())))
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    /** 查询订单明细实体列表 */
    private List<ShopOrderItem> listOrderItems(Long orderId) {
        return orderItemMapper.selectList(
                new LambdaQueryWrapper<ShopOrderItem>().eq(ShopOrderItem::getOrderId, orderId));
    }

    /** 查询订单明细 VO 列表 */
    private List<OrderItemVO> listItems(Long orderId) {
        return listOrderItems(orderId).stream().map(this::toItemVO).collect(Collectors.toList());
    }

    /** 查询店铺名称 */
    private String loadShopName(Long merchantId) {
        Merchant merchant = merchantMapper.selectById(merchantId);
        return merchant == null ? null : merchant.getShopName();
    }

    /** 订单号：时间戳 + 6位随机数字 */
    private String generateOrderNo() {
        return LocalDateTime.now().format(ORDER_NO_FORMAT) + String.format("%06d", RANDOM.nextInt(1000000));
    }

    /** 取货码：6位随机数字，到店核销凭证 */
    private String generatePickupCode() {
        return String.format("%06d", RANDOM.nextInt(1000000));
    }

    /** DO 转 VO，附加店铺名称、状态与取货方式名称、明细列表 */
    private OrderVO toVO(ShopOrder order, String shopName, List<OrderItemVO> items) {
        OrderVO vo = new OrderVO();
        BeanUtils.copyProperties(order, vo);
        vo.setShopName(shopName);
        OrderStatus status = OrderStatus.of(order.getStatus());
        vo.setStatusName(status == null ? null : status.getDesc());
        PickupType pickupType = PickupType.of(order.getPickupType());
        vo.setPickupTypeName(pickupType == null ? null : pickupType.getDesc());
        vo.setItems(items);
        return vo;
    }

    /** 明细 DO 转 VO */
    private OrderItemVO toItemVO(ShopOrderItem item) {
        OrderItemVO vo = new OrderItemVO();
        BeanUtils.copyProperties(item, vo);
        return vo;
    }
}
