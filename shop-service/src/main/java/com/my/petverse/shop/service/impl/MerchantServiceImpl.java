package com.my.petverse.shop.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.shop.MerchantAdminPageQueryDTO;
import com.my.petverse.common.dto.shop.MerchantBanDTO;
import com.my.petverse.common.dto.shop.MerchantStatusDTO;
import com.my.petverse.common.dto.shop.MerchantUpdateDTO;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.enums.UserRole;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.MerchantWithdrawnMessage;
import com.my.petverse.common.mq.message.ProductIndexMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.shop.MerchantAdminVO;
import com.my.petverse.common.vo.shop.MerchantVO;
import com.my.petverse.common.vo.shop.StoreVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.shop.feign.UserFeignClient;
import com.my.petverse.shop.mapper.CartItemMapper;
import com.my.petverse.shop.mapper.MerchantApplyMapper;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.mapper.ProductMapper;
import com.my.petverse.shop.mapper.ProductReviewMapper;
import com.my.petverse.shop.mapper.ProductReviewReplyMapper;
import com.my.petverse.shop.search.ProductSearchService;
import com.my.petverse.shop.service.MerchantService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.stream.Collectors;

/**
 * 商家店铺服务实现类
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class MerchantServiceImpl extends ServiceImpl<MerchantMapper, Merchant> implements MerchantService {

    private final MqEventPublisher mqEventPublisher;

    private final ProductSearchService productSearchService;

    private final UserFeignClient userFeignClient;

    private final ProductMapper productMapper;

    private final CartItemMapper cartItemMapper;

    private final ProductReviewMapper productReviewMapper;

    private final ProductReviewReplyMapper productReviewReplyMapper;

    private final MerchantApplyMapper merchantApplyMapper;

    /** 撤销冷静期天数，默认 5 天 */
    @Value("${petverse.merchant.withdraw-cooling-days:5}")
    private int withdrawCoolingDays;

    /** 单轮清理的到期商家数量上限，避免任务执行过久 */
    private static final int PURGE_BATCH_SIZE = 20;

    /** 店铺状态：打烊 */
    private static final int STATUS_CLOSED = 0;
    /** 店铺状态：封禁 */
    private static final int STATUS_BANNED = 2;

    @Override
    public MerchantVO getByUserId(Long userId) {
        Merchant merchant = getOne(new LambdaQueryWrapper<Merchant>().eq(Merchant::getUserId, userId));
        if (merchant == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "您尚未入驻，请先提交入驻申请");
        }
        return toVO(merchant);
    }

    @Override
    public MerchantVO updateInfo(Long userId, MerchantUpdateDTO dto) {
        Merchant merchant = getEditableMerchant(userId);
        String originalShopName = merchant.getShopName();
        // 仅更新传入的非空字段
        if (StringUtils.hasText(dto.getShopName())) {
            merchant.setShopName(dto.getShopName());
        }
        if (StringUtils.hasText(dto.getShopLogo())) {
            merchant.setShopLogo(dto.getShopLogo());
        }
        if (dto.getDescription() != null) {
            merchant.setDescription(dto.getDescription());
        }
        if (StringUtils.hasText(dto.getContactPhone())) {
            merchant.setContactPhone(dto.getContactPhone());
        }
        updateById(merchant);
        // 店铺改名后发事件，由索引消费者异步重刷商品索引，保证按店铺名搜商品仍然准确（重复执行幂等）
        if (!Objects.equals(originalShopName, merchant.getShopName())) {
            refreshProductIndex(merchant.getId());
        }
        return toVO(merchant);
    }

    @Override
    public Merchant getManageMerchant(Long userId) {
        Merchant merchant = getOne(new LambdaQueryWrapper<Merchant>().eq(Merchant::getUserId, userId));
        if (merchant == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "您尚未入驻，请先提交入驻申请");
        }
        if (merchant.isBanned()) {
            throw new BusinessException(ResultCode.FORBIDDEN, "店铺已被封禁，请联系管理员");
        }
        return merchant;
    }

    @Override
    public Merchant getEditableMerchant(Long userId) {
        Merchant merchant = getManageMerchant(userId);
        if (merchant.isWithdrawing()) {
            throw new BusinessException(ResultCode.FORBIDDEN, "商家身份撤销冷静期中，仅可查看数据或取消撤销");
        }
        return merchant;
    }

    @Override
    public MerchantVO updateSellingStatus(Long userId, MerchantStatusDTO dto) {
        Merchant merchant = getEditableMerchant(userId);
        if (Objects.equals(merchant.getStatus(), dto.getStatus())) {
            return toVO(merchant);
        }
        merchant.setStatus(dto.getStatus());
        updateById(merchant);
        // 打烊/营业影响商品在商城是否可见，异步刷新该店商品索引（ES 不可用时由数据库查询兜底过滤）
        refreshProductIndex(merchant.getId());
        return toVO(merchant);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public MerchantVO applyWithdraw(Long userId) {
        Merchant merchant = getOne(new LambdaQueryWrapper<Merchant>().eq(Merchant::getUserId, userId));
        if (merchant == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "您尚未入驻，请先提交入驻申请");
        }
        if (merchant.isBanned()) {
            throw new BusinessException(ResultCode.FORBIDDEN, "店铺已被封禁，无法申请撤销，请联系管理员");
        }
        if (merchant.isWithdrawing()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "已提交撤销申请，请勿重复操作");
        }
        merchant.setWithdrawStatus(1);
        merchant.setWithdrawDeadline(LocalDateTime.now().plusDays(withdrawCoolingDays));
        updateById(merchant);
        // 冷静期内店铺立即停止售卖，刷新索引隐藏商品
        refreshProductIndex(merchant.getId());
        return toVO(merchant);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public MerchantVO cancelWithdraw(Long userId) {
        Merchant merchant = getOne(new LambdaQueryWrapper<Merchant>().eq(Merchant::getUserId, userId));
        if (merchant == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "您尚未入驻，请先提交入驻申请");
        }
        if (!merchant.isWithdrawing()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "当前未申请撤销商家身份");
        }
        if (merchant.getWithdrawDeadline() != null
                && !LocalDateTime.now().isBefore(merchant.getWithdrawDeadline())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "撤销冷静期已结束，无法恢复");
        }
        // updateById 默认忽略 null 字段，截止时间必须用 UpdateWrapper 显式清空
        baseMapper.update(null, new LambdaUpdateWrapper<Merchant>()
                .eq(Merchant::getId, merchant.getId())
                .set(Merchant::getWithdrawStatus, 0)
                .set(Merchant::getWithdrawDeadline, null));
        merchant.setWithdrawStatus(0);
        merchant.setWithdrawDeadline(null);
        refreshProductIndex(merchant.getId());
        return toVO(merchant);
    }

    @Override
    public StoreVO getShopById(Long merchantId) {
        Merchant merchant = getById(merchantId);
        if (merchant == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "店铺不存在");
        }
        StoreVO vo = new StoreVO();
        BeanUtils.copyProperties(merchant, vo);
        vo.setSelling(merchant.isSelling());
        return vo;
    }

    @Override
    public PageResult<MerchantAdminVO> pageForAdmin(MerchantAdminPageQueryDTO dto) {
        Page<Merchant> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<Merchant>()
                        .eq(dto.getStatus() != null, Merchant::getStatus, dto.getStatus())
                        .eq(Boolean.TRUE.equals(dto.getWithdrawing()), Merchant::getWithdrawStatus, 1)
                        .and(Boolean.FALSE.equals(dto.getWithdrawing()),
                                w -> w.eq(Merchant::getWithdrawStatus, 0)
                                        .or().isNull(Merchant::getWithdrawStatus))
                        .like(StringUtils.hasText(dto.getShopName()), Merchant::getShopName, dto.getShopName())
                        .orderByDesc(Merchant::getCreateTime));
        List<MerchantAdminVO> vos = page.getRecords().stream()
                .map(this::toAdminVO)
                .collect(Collectors.toList());
        fillOwnerNickname(vos);
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void ban(Long merchantId, MerchantBanDTO dto) {
        Merchant merchant = getById(merchantId);
        if (merchant == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "店铺不存在");
        }
        if (merchant.isWithdrawing()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "商家正在撤销冷静期，无法封禁");
        }
        if (merchant.isBanned()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "店铺已被封禁，请刷新列表");
        }
        merchant.setStatus(STATUS_BANNED);
        merchant.setBanReason(dto.getReason().trim());
        merchant.setBanTime(LocalDateTime.now());
        updateById(merchant);
        // 封禁后商城立即隐藏该店商品
        refreshProductIndex(merchant.getId());
        log.info("管理员封禁店铺，merchantId={}, shopName={}, reason={}",
                merchant.getId(), merchant.getShopName(), merchant.getBanReason());
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void unban(Long merchantId) {
        Merchant merchant = getById(merchantId);
        if (merchant == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "店铺不存在");
        }
        if (!merchant.isBanned()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "店铺未被封禁，请刷新列表");
        }
        // 解封后回到打烊状态，由商家自行确认后重新营业，避免违规商品立刻重新上架
        baseMapper.update(null, new LambdaUpdateWrapper<Merchant>()
                .eq(Merchant::getId, merchant.getId())
                .set(Merchant::getStatus, STATUS_CLOSED)
                .set(Merchant::getBanReason, null)
                .set(Merchant::getBanTime, null));
        merchant.setStatus(STATUS_CLOSED);
        merchant.setBanReason(null);
        merchant.setBanTime(null);
        refreshProductIndex(merchant.getId());
        log.info("管理员解封店铺，merchantId={}, shopName={}", merchant.getId(), merchant.getShopName());
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public int purgeExpiredWithdrawals() {
        List<Merchant> expired = list(new LambdaQueryWrapper<Merchant>()
                .eq(Merchant::getWithdrawStatus, 1)
                .le(Merchant::getWithdrawDeadline, LocalDateTime.now())
                .last("LIMIT " + PURGE_BATCH_SIZE));
        for (Merchant merchant : expired) {
            purgeMerchant(merchant);
        }
        return expired.size();
    }

    /**
     * 清理单个到期商家：物理删除店铺及其商品、评价、评价回复、购物车关联与入驻申请；
     * 订单保留作为买家的交易记录；商品索引删除与用户角色降级在事务提交后异步执行
     */
    private void purgeMerchant(Merchant merchant) {
        Long merchantId = merchant.getId();
        Long userId = merchant.getUserId();
        List<Long> productIds = productMapper.selectList(new LambdaQueryWrapper<Product>()
                        .select(Product::getId)
                        .eq(Product::getMerchantId, merchantId)).stream()
                .map(Product::getId)
                .collect(Collectors.toList());
        if (!productIds.isEmpty()) {
            cartItemMapper.physicalDeleteByProductIds(productIds);
        }
        List<Long> reviewIds = productReviewMapper.selectIdsByMerchantId(merchantId);
        if (!reviewIds.isEmpty()) {
            productReviewReplyMapper.physicalDeleteByReviewIds(reviewIds);
        }
        productReviewMapper.physicalDeleteByMerchantId(merchantId);
        productMapper.physicalDeleteByMerchantId(merchantId);
        merchantApplyMapper.physicalDeleteByUserId(userId);
        baseMapper.physicalDeleteById(merchantId);

        // 商品文档从检索索引中移除；MQ 不可用或发送失败时直接本地删除，避免搜索结果残留
        for (Long productId : productIds) {
            mqEventPublisher.publishAfterCommit(MqTopics.PRODUCT_INDEX, MqTopics.TAG_PRODUCT_REMOVE,
                    new ProductIndexMessage(productId, merchantId), "product-index:" + productId,
                    () -> removeProductSafely(productId));
        }
        // 用户角色降级为普通用户，跨服务消费失败时回退为直接调用用户服务；
        // 兜底调用异常只记日志，数据清理已完成，不能因角色同步失败反复重跑清理任务
        mqEventPublisher.publishAfterCommit(MqTopics.MERCHANT_EVENT, MqTopics.TAG_MERCHANT_WITHDRAWN,
                new MerchantWithdrawnMessage(merchantId, userId), "merchant-withdrawn:" + merchantId,
                () -> downgradeRoleSafely(userId));
        log.info("商家撤销冷静期结束，已清除店铺数据，merchantId={}, userId={}, productCount={}",
                merchantId, userId, productIds.size());
    }

    /** 本地兜底删除商品索引，异常不向外抛出 */
    private void removeProductSafely(Long productId) {
        try {
            productSearchService.removeProduct(productId);
        } catch (Exception e) {
            log.warn("兜底删除商品索引失败，productId={}", productId, e);
        }
    }

    /** 本地兜底降级用户角色，异常不向外抛出，避免影响定时清理任务后续批次 */
    private void downgradeRoleSafely(Long userId) {
        try {
            Result<Boolean> result = userFeignClient.upgradeRole(userId, UserRole.USER.name());
            if (result == null || result.getCode() != ResultCode.SUCCESS.getCode()) {
                log.warn("兜底降级商家角色失败，需人工检查，userId={}, result={}", userId, result);
            }
        } catch (Exception e) {
            log.warn("兜底降级商家角色异常，需人工检查，userId={}", userId, e);
        }
    }

    /** 店铺状态变更后异步重刷商品索引（含店铺名与可售状态），MQ 不可用时本地直接刷新 */
    private void refreshProductIndex(Long merchantId) {
        mqEventPublisher.publishAfterCommit(MqTopics.PRODUCT_INDEX, MqTopics.TAG_PRODUCT_SHOP_CHANGED,
                new ProductIndexMessage(null, merchantId), "product-shop-changed:" + merchantId,
                () -> productSearchService.refreshMerchantProducts(merchantId));
    }

    /** 批量聚合店主昵称：整页店铺去重后一次查询，用户服务不可用时降级为不展示 */
    private void fillOwnerNickname(List<MerchantAdminVO> vos) {
        if (vos.isEmpty()) {
            return;
        }
        List<Long> userIds = vos.stream()
                .map(MerchantAdminVO::getUserId)
                .filter(Objects::nonNull)
                .distinct()
                .collect(Collectors.toList());
        if (userIds.isEmpty()) {
            return;
        }
        Map<Long, String> nicknames = new HashMap<>();
        try {
            Result<List<UserVO>> result = userFeignClient.listByIds(userIds);
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode() && result.getData() != null) {
                result.getData().forEach(user -> {
                    if (user.getId() != null) {
                        nicknames.put(user.getId(), user.getNickname());
                    }
                });
            }
        } catch (Exception e) {
            log.warn("批量查询店主信息失败，用户数={}", userIds.size(), e);
        }
        if (nicknames.isEmpty()) {
            return;
        }
        vos.forEach(item -> {
            String nickname = nicknames.get(item.getUserId());
            if (nickname != null) {
                item.setOwnerNickname(nickname);
            }
        });
    }

    /** DO 转 VO */
    private MerchantVO toVO(Merchant merchant) {
        MerchantVO vo = new MerchantVO();
        BeanUtils.copyProperties(merchant, vo);
        vo.setSelling(merchant.isSelling());
        return vo;
    }

    /** DO 转管理端 VO */
    private MerchantAdminVO toAdminVO(Merchant merchant) {
        MerchantAdminVO vo = new MerchantAdminVO();
        BeanUtils.copyProperties(merchant, vo);
        vo.setSelling(merchant.isSelling());
        return vo;
    }
}
