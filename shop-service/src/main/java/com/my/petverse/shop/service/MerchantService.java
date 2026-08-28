package com.my.petverse.shop.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.shop.MerchantUpdateDTO;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.vo.shop.MerchantVO;

/**
 * 商家店铺服务接口
 */
public interface MerchantService extends IService<Merchant> {

    /**
     * 查询当前商家的店铺信息，未入驻抛业务异常
     *
     * @param userId 店主用户ID
     * @return 店铺信息
     */
    MerchantVO getByUserId(Long userId);

    /**
     * 修改当前商家的店铺信息（名称/LOGO/简介/联系电话）
     *
     * @param userId 店主用户ID
     * @param dto    修改参数
     * @return 修改后的店铺信息
     */
    MerchantVO updateInfo(Long userId, MerchantUpdateDTO dto);

    /**
     * 查询当前商家的店铺实体并校验营业状态（商品管理前置校验）
     *
     * @param userId 店主用户ID
     * @return 店铺实体
     */
    Merchant getActiveMerchant(Long userId);

    /**
     * 查询店铺公开信息（买家浏览店家页面，仅营业中店铺可见）
     *
     * @param merchantId 商家ID
     * @return 店铺信息
     */
    MerchantVO getShopById(Long merchantId);
}
