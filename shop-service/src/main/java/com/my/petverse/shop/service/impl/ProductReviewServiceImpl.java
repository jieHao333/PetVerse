package com.my.petverse.shop.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.dto.shop.ProductReviewPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductReviewReplyPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductReviewReplySaveDTO;
import com.my.petverse.common.dto.shop.ProductReviewSaveDTO;
import com.my.petverse.common.dto.shop.ProductReviewUpdateDTO;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.entity.shop.ProductReview;
import com.my.petverse.common.entity.shop.ProductReviewReply;
import com.my.petverse.common.entity.shop.ShopOrder;
import com.my.petverse.common.enums.NotificationSource;
import com.my.petverse.common.enums.NotificationType;
import com.my.petverse.common.enums.OrderStatus;
import com.my.petverse.common.enums.ProductCategory;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.NotifyMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.shop.MyReviewVO;
import com.my.petverse.common.vo.shop.ProductReviewReplyVO;
import com.my.petverse.common.vo.shop.ProductReviewSummaryVO;
import com.my.petverse.common.vo.shop.ProductReviewVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.shop.feign.UserFeignClient;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import com.my.petverse.shop.mapper.ProductReviewMapper;
import com.my.petverse.shop.mapper.ProductReviewReplyMapper;
import com.my.petverse.shop.mapper.ShopOrderMapper;
import com.my.petverse.shop.service.ProductReviewService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 商品评价服务实现类：
 * 评价资格 = 当前用户存在包含该商品的已完成订单，且同一商品仅能评价一次；
 * 评价人昵称/头像通过 Feign 聚合，用户服务不可用时降级为不展示
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ProductReviewServiceImpl extends ServiceImpl<ProductReviewMapper, ProductReview>
        implements ProductReviewService {

    private final ProductMapper productMapper;

    private final MerchantMapper merchantMapper;

    private final ShopOrderMapper shopOrderMapper;

    private final ProductReviewReplyMapper replyMapper;

    private final UserFeignClient userFeignClient;

    private final MqEventPublisher mqEventPublisher;

    @Override
    public ProductReviewVO saveReview(Long userId, ProductReviewSaveDTO dto) {
        Product product = productMapper.selectById(dto.getProductId());
        if (product == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "商品不存在");
        }
        // 同一用户对同一商品仅能评价一次（表唯一键 uk_product_user 兜底）
        Long reviewed = baseMapper.selectCount(new LambdaQueryWrapper<ProductReview>()
                .eq(ProductReview::getProductId, dto.getProductId())
                .eq(ProductReview::getUserId, userId));
        if (reviewed != null && reviewed > 0) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "您已评价过该商品，无法重复评价");
        }
        // 评价资格校验：必须存在包含该商品的已完成订单（到店核销后）
        ShopOrder completedOrder = findCompletedOrder(userId, dto.getProductId());
        if (completedOrder == null) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "购买并完成取货后才能评价该商品");
        }
        ProductReview review = new ProductReview();
        review.setProductId(dto.getProductId());
        review.setMerchantId(product.getMerchantId());
        review.setUserId(userId);
        review.setOrderId(completedOrder.getId());
        review.setRating(dto.getRating());
        review.setContent(dto.getContent());
        applyMedia(review, dto.getImageUrls(), dto.getVideoUrl());
        try {
            save(review);
        } catch (DuplicateKeyException e) {
            // 并发重复提交被唯一键拦截，转为业务提示（含已删除评价：删除后同样无法重新评价）
            throw new BusinessException(ResultCode.BAD_REQUEST, "您已评价过该商品，评价删除后也无法重新评价");
        }
        return toVO(review, loadUserInfo(userId));
    }

    @Override
    public ProductReviewVO updateReview(Long userId, ProductReviewUpdateDTO dto) {
        ProductReview review = getById(dto.getId());
        // 仅评价人本人可修改，防止越权篡改他人评价
        if (review == null || !Objects.equals(review.getUserId(), userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "评价不存在");
        }
        review.setRating(dto.getRating());
        review.setContent(dto.getContent());
        // 用 LambdaUpdateWrapper 显式 set，保证媒体字段被清空（传空列表/空串）时能写入 NULL
        // （updateById 默认忽略 null 字段，无法清空）
        LambdaUpdateWrapper<ProductReview> wrapper = new LambdaUpdateWrapper<ProductReview>()
                .eq(ProductReview::getId, review.getId())
                .set(ProductReview::getRating, dto.getRating())
                .set(ProductReview::getContent, dto.getContent())
                .set(ProductReview::getImageUrls, dto.getImageUrls() == null || dto.getImageUrls().isEmpty()
                        ? null : String.join(",", dto.getImageUrls()))
                .set(ProductReview::getVideoUrl, StringUtils.hasText(dto.getVideoUrl()) ? dto.getVideoUrl() : null);
        update(wrapper);
        review.setImageUrls(dto.getImageUrls() == null || dto.getImageUrls().isEmpty()
                ? null : String.join(",", dto.getImageUrls()));
        review.setVideoUrl(StringUtils.hasText(dto.getVideoUrl()) ? dto.getVideoUrl() : null);
        return toVO(review, loadUserInfo(userId));
    }

    @Override
    public boolean deleteReview(Long userId, Long reviewId) {
        ProductReview review = getById(reviewId);
        // 仅评价人本人可删除，防止越权删除他人评价
        if (review == null || !Objects.equals(review.getUserId(), userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "评价不存在");
        }
        return removeById(reviewId);
    }

    @Override
    public PageResult<ProductReviewVO> pageReviews(ProductReviewPageQueryDTO dto) {
        Page<ProductReview> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<ProductReview>()
                        .eq(ProductReview::getProductId, dto.getProductId())
                        .eq(dto.getRating() != null, ProductReview::getRating, dto.getRating())
                        .orderByDesc(ProductReview::getCreateTime));
        List<ProductReview> reviews = page.getRecords();
        if (reviews.isEmpty()) {
            return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), List.of());
        }
        // 按用户去重后批量聚合昵称与头像，避免同一评价人重复调用用户服务
        Map<Long, UserVO> users = new HashMap<>();
        reviews.stream().map(ProductReview::getUserId).distinct()
                .forEach(userId -> users.put(userId, loadUserInfo(userId)));
        List<ProductReviewVO> vos = reviews.stream()
                .map(review -> toVO(review, users.get(review.getUserId())))
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    public PageResult<MyReviewVO> pageMyReviews(Long userId, BasePageQuery dto) {
        Page<ProductReview> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<ProductReview>()
                        .eq(ProductReview::getUserId, userId)
                        .orderByDesc(ProductReview::getCreateTime));
        List<ProductReview> reviews = page.getRecords();
        if (reviews.isEmpty()) {
            return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), List.of());
        }
        // 批量聚合商品信息（商品可能已下架/删除，逻辑删除后查不到，前端降级展示）
        Set<Long> productIds = reviews.stream().map(ProductReview::getProductId).collect(Collectors.toSet());
        Map<Long, Product> products = productMapper.selectBatchIds(productIds).stream()
                .collect(Collectors.toMap(Product::getId, product -> product));
        // 批量聚合店铺名称
        Set<Long> merchantIds = reviews.stream().map(ProductReview::getMerchantId)
                .filter(Objects::nonNull).collect(Collectors.toSet());
        Map<Long, Merchant> merchants = merchantIds.isEmpty() ? Map.of()
                : merchantMapper.selectBatchIds(merchantIds).stream()
                        .collect(Collectors.toMap(Merchant::getId, merchant -> merchant));
        // 批量统计每条评价的回复互动数（一条 group by SQL，避免逐条 count）
        Set<Long> reviewIds = reviews.stream().map(ProductReview::getId).collect(Collectors.toSet());
        Map<Long, Long> replyCounts = replyMapper.selectMaps(new QueryWrapper<ProductReviewReply>()
                        .select("review_id AS reviewId", "COUNT(*) AS cnt")
                        .in("review_id", reviewIds)
                        .groupBy("review_id"))
                .stream().collect(Collectors.toMap(
                        row -> Long.valueOf(row.get("reviewId").toString()),
                        row -> ((Number) row.get("cnt")).longValue()));
        List<MyReviewVO> vos = reviews.stream().map(review -> {
            MyReviewVO vo = new MyReviewVO();
            vo.setReviewId(review.getId());
            vo.setProductId(review.getProductId());
            Product product = products.get(review.getProductId());
            if (product != null) {
                vo.setProductName(product.getName());
                vo.setProductImage(product.getImageUrl());
                vo.setCategory(product.getCategory());
                ProductCategory category = ProductCategory.of(product.getCategory());
                vo.setCategoryName(category == null ? null : category.getDesc());
            }
            Merchant merchant = review.getMerchantId() == null ? null : merchants.get(review.getMerchantId());
            vo.setShopName(merchant == null ? null : merchant.getShopName());
            vo.setRating(review.getRating());
            vo.setContent(review.getContent());
            if (StringUtils.hasText(review.getImageUrls())) {
                vo.setImageUrls(Arrays.asList(review.getImageUrls().split(",")));
            }
            vo.setVideoUrl(review.getVideoUrl());
            vo.setReplyCount(replyCounts.getOrDefault(review.getId(), 0L));
            vo.setCreateTime(review.getCreateTime());
            return vo;
        }).collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    public ProductReviewSummaryVO getSummary(Long productId, Long userId) {
        // 单条聚合 SQL 同时取总数与平均分，避免加载全量评价
        Map<String, Object> stats = baseMapper.selectMaps(new QueryWrapper<ProductReview>()
                        .select("COUNT(*) AS total", "AVG(rating) AS avgRating")
                        .eq("product_id", productId))
                .stream().findFirst().orElse(Map.of());
        long total = stats.get("total") == null ? 0L : ((Number) stats.get("total")).longValue();
        BigDecimal avgRating = stats.get("avgRating") == null ? BigDecimal.ZERO
                : new BigDecimal(stats.get("avgRating").toString()).setScale(1, RoundingMode.HALF_UP);
        ProductReviewSummaryVO vo = new ProductReviewSummaryVO();
        vo.setProductId(productId);
        vo.setTotalCount(total);
        vo.setAvgRating(avgRating);
        vo.setCanReview(false);
        vo.setReviewed(false);
        if (userId != null) {
            boolean reviewed = baseMapper.selectCount(new LambdaQueryWrapper<ProductReview>()
                    .eq(ProductReview::getProductId, productId)
                    .eq(ProductReview::getUserId, userId)) > 0;
            vo.setReviewed(reviewed);
            // 曾评价过（含已删除）即占用唯一键槽位，删除后也无法重新评价
            boolean everReviewed = baseMapper.countIncludingDeleted(productId, userId) > 0;
            vo.setEverReviewed(everReviewed);
            // 从未评价过（含历史）且存在已完成订单才具备评价资格
            vo.setCanReview(!everReviewed && findCompletedOrder(userId, productId) != null);
        }
        return vo;
    }

    @Override
    public ProductReviewReplyVO saveReply(Long userId, ProductReviewReplySaveDTO dto) {
        ProductReview review = getById(dto.getReviewId());
        if (review == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "评价不存在或已删除");
        }
        ProductReviewReply reply = new ProductReviewReply();
        reply.setReviewId(dto.getReviewId());
        reply.setUserId(userId);
        reply.setReplyUserId(dto.getReplyUserId());
        reply.setContent(dto.getContent());
        replyMapper.insert(reply);
        // 回复他人评价时通知被回复人（自己回复自己不通知），跳转目标为商品，去重键用回复ID
        if (dto.getReplyUserId() != null && !dto.getReplyUserId().equals(userId)) {
            NotifyMessage notify = new NotifyMessage(dto.getReplyUserId(), userId,
                    NotificationType.REPLY.getCode(), NotificationSource.SHOP_REVIEW.getCode(),
                    review.getProductId(), dto.getContent());
            mqEventPublisher.publish(MqTopics.NOTIFY, MqTopics.TAG_NOTIFY_CREATED,
                    notify, String.valueOf(reply.getId()));
        }
        // 一次查询同时覆盖回复人与被回复人的昵称头像
        Set<Long> userIds = new HashSet<>();
        userIds.add(userId);
        if (dto.getReplyUserId() != null) {
            userIds.add(dto.getReplyUserId());
        }
        Map<Long, UserVO> users = loadUserInfos(userIds);
        return toReplyVO(reply, users);
    }

    @Override
    public PageResult<ProductReviewReplyVO> pageReplies(ProductReviewReplyPageQueryDTO dto) {
        Page<ProductReviewReply> page = replyMapper.selectPage(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<ProductReviewReply>()
                        .eq(ProductReviewReply::getReviewId, dto.getReviewId())
                        // 按时间正序展示互动过程
                        .orderByAsc(ProductReviewReply::getCreateTime));
        List<ProductReviewReply> replies = page.getRecords();
        if (replies.isEmpty()) {
            return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), List.of());
        }
        // 回复人与被回复人去重后批量聚合昵称头像，避免同一用户重复调用用户服务
        Set<Long> userIds = new HashSet<>();
        replies.forEach(reply -> {
            userIds.add(reply.getUserId());
            if (reply.getReplyUserId() != null) {
                userIds.add(reply.getReplyUserId());
            }
        });
        Map<Long, UserVO> users = loadUserInfos(userIds);
        List<ProductReviewReplyVO> vos = replies.stream()
                .map(reply -> toReplyVO(reply, users))
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    public boolean deleteReply(Long userId, Long replyId) {
        ProductReviewReply reply = replyMapper.selectById(replyId);
        // 仅回复人本人可删除，防止越权删除他人回复
        if (reply == null || !Objects.equals(reply.getUserId(), userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "回复不存在");
        }
        return replyMapper.deleteById(replyId) > 0;
    }

    /** 查询用户包含指定商品的最近一笔已完成订单，不存在返回 null */
    private ShopOrder findCompletedOrder(Long userId, Long productId) {
        return shopOrderMapper.selectOne(new LambdaQueryWrapper<ShopOrder>()
                .eq(ShopOrder::getUserId, userId)
                .eq(ShopOrder::getStatus, OrderStatus.COMPLETED.getCode())
                // 订单明细表反查包含该商品的订单（同库子查询）
                .inSql(ShopOrder::getId, "SELECT order_id FROM shop_order_item WHERE product_id = "
                        + productId + " AND deleted = 0")
                .orderByDesc(ShopOrder::getFinishTime)
                .last("LIMIT 1"));
    }

    /** Feign 查询用户信息，用户服务不可用时降级返回 null（评价列表不展示昵称头像） */
    private UserVO loadUserInfo(Long userId) {
        if (userId == null) {
            return null;
        }
        try {
            Result<UserVO> result = userFeignClient.getUserById(userId);
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode()) {
                return result.getData();
            }
        } catch (Exception e) {
            log.warn("查询评价人信息失败 userId={}", userId, e);
        }
        return null;
    }

    /** 批量查询用户信息（去重 + 逐个 Feign + 降级容错），供回复列表聚合使用 */
    private Map<Long, UserVO> loadUserInfos(Collection<Long> userIds) {
        Map<Long, UserVO> users = new HashMap<>();
        for (Long userId : userIds) {
            if (userId != null) {
                users.put(userId, loadUserInfo(userId));
            }
        }
        return users;
    }

    /** 写入评价媒体字段：图片列表转逗号分隔串存储，视频单地址 */
    private void applyMedia(ProductReview review, List<String> imageUrls, String videoUrl) {
        review.setImageUrls(imageUrls == null || imageUrls.isEmpty()
                ? null : String.join(",", imageUrls));
        review.setVideoUrl(StringUtils.hasText(videoUrl) ? videoUrl : null);
    }

    /** DO 转 VO，附加评价人昵称与头像，图片串还原为列表 */
    private ProductReviewVO toVO(ProductReview review, UserVO user) {
        ProductReviewVO vo = new ProductReviewVO();
        BeanUtils.copyProperties(review, vo);
        if (StringUtils.hasText(review.getImageUrls())) {
            vo.setImageUrls(Arrays.asList(review.getImageUrls().split(",")));
        }
        if (user != null) {
            vo.setUserNickname(user.getNickname());
            vo.setUserAvatar(user.getAvatar());
        }
        return vo;
    }

    /** 回复 DO 转 VO，从聚合结果中附加回复人与被回复人昵称头像 */
    private ProductReviewReplyVO toReplyVO(ProductReviewReply reply, Map<Long, UserVO> users) {
        ProductReviewReplyVO vo = new ProductReviewReplyVO();
        BeanUtils.copyProperties(reply, vo);
        UserVO user = users.get(reply.getUserId());
        if (user != null) {
            vo.setUserNickname(user.getNickname());
            vo.setUserAvatar(user.getAvatar());
        }
        if (reply.getReplyUserId() != null) {
            UserVO replyUser = users.get(reply.getReplyUserId());
            vo.setReplyUserNickname(replyUser == null ? null : replyUser.getNickname());
        }
        return vo;
    }
}
