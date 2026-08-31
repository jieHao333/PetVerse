package com.my.petverse.shop.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.shop.MerchantUpdateDTO;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.ProductIndexMessage;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.shop.MerchantVO;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.service.MerchantService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.util.Objects;

/**
 * 商家店铺服务实现类
 */
@Service
@RequiredArgsConstructor
public class MerchantServiceImpl extends ServiceImpl<MerchantMapper, Merchant> implements MerchantService {

    private final MqEventPublisher mqEventPublisher;

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
        Merchant merchant = getActiveMerchant(userId);
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
            mqEventPublisher.publishAfterCommit(MqTopics.PRODUCT_INDEX, MqTopics.TAG_PRODUCT_SHOP_RENAMED,
                    new ProductIndexMessage(null, merchant.getId()),
                    "shop-renamed:" + merchant.getId());
        }
        return toVO(merchant);
    }

    @Override
    public Merchant getActiveMerchant(Long userId) {
        Merchant merchant = getOne(new LambdaQueryWrapper<Merchant>().eq(Merchant::getUserId, userId));
        if (merchant == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "您尚未入驻，请先提交入驻申请");
        }
        if (merchant.getStatus() == null || merchant.getStatus() != 1) {
            throw new BusinessException(ResultCode.FORBIDDEN, "店铺已被封禁，请联系管理员");
        }
        return merchant;
    }

    @Override
    public MerchantVO getShopById(Long merchantId) {
        Merchant merchant = getById(merchantId);
        // 封禁店铺对买家不可见，统一提示不存在
        if (merchant == null || merchant.getStatus() == null || merchant.getStatus() != 1) {
            throw new BusinessException(ResultCode.NOT_FOUND, "店铺不存在或已停止营业");
        }
        return toVO(merchant);
    }

    /** DO 转 VO */
    private MerchantVO toVO(Merchant merchant) {
        MerchantVO vo = new MerchantVO();
        BeanUtils.copyProperties(merchant, vo);
        return vo;
    }
}
