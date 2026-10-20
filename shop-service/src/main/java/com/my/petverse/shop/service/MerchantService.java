package com.my.petverse.shop.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.shop.MerchantAdminPageQueryDTO;
import com.my.petverse.common.dto.shop.MerchantBanDTO;
import com.my.petverse.common.dto.shop.MerchantStatusDTO;
import com.my.petverse.common.dto.shop.MerchantUpdateDTO;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.shop.MerchantAdminVO;
import com.my.petverse.common.vo.shop.MerchantVO;
import com.my.petverse.common.vo.shop.StoreVO;

/**
 * 商家店铺服务接口
 */
public interface MerchantService extends IService<Merchant> {

    /**
     * 查询当前商家的店铺信息（含封禁与撤销冷静期状态），未入驻抛业务异常
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
     * 查询商家店铺并校验可管理（未封禁），用于订单查看/核销等允许打烊期间进行的操作
     *
     * @param userId 店主用户ID
     * @return 店铺实体
     */
    Merchant getManageMerchant(Long userId);

    /**
     * 查询商家店铺并校验可编辑（未封禁且不在撤销冷静期），用于商品与店铺信息变更
     *
     * @param userId 店主用户ID
     * @return 店铺实体
     */
    Merchant getEditableMerchant(Long userId);

    /**
     * 商家切换店铺营业状态（营业/打烊），封禁与撤销冷静期内不可操作
     *
     * @param userId 店主用户ID
     * @param dto    目标状态
     * @return 修改后的店铺信息
     */
    MerchantVO updateSellingStatus(Long userId, MerchantStatusDTO dto);

    /**
     * 商家申请撤销身份，进入 5 天冷静期，期间店铺立即停止对外售卖
     *
     * @param userId 店主用户ID
     * @return 店铺信息（含冷静期截止时间）
     */
    MerchantVO applyWithdraw(Long userId);

    /**
     * 冷静期内取消撤销，恢复正常商家身份（店铺营业状态保持撤销前设置）
     *
     * @param userId 店主用户ID
     * @return 店铺信息
     */
    MerchantVO cancelWithdraw(Long userId);

    /**
     * 查询店铺公开信息（买家店家页面，封禁/打烊店铺仍可展示状态提示）
     *
     * @param merchantId 商家ID
     * @return 店铺公开信息
     */
    StoreVO getShopById(Long merchantId);

    /**
     * 管理端分页查询商家店铺（按状态/撤销中/店铺名筛选）
     *
     * @param dto 分页查询参数
     * @return 店铺分页结果
     */
    PageResult<MerchantAdminVO> pageForAdmin(MerchantAdminPageQueryDTO dto);

    /**
     * 管理员封禁店铺：封禁后商城隐藏该店全部商品且禁止下单
     *
     * @param merchantId 商家ID
     * @param dto        封禁原因
     */
    void ban(Long merchantId, MerchantBanDTO dto);

    /**
     * 管理员解封店铺：解封后店铺恢复为打烊状态，由商家自行重新营业
     *
     * @param merchantId 商家ID
     */
    void unban(Long merchantId);

    /**
     * 清理已到期未撤回的撤销申请：彻底删除店铺、商品、评价、购物车关联与入驻申请，并降级用户角色
     *
     * @return 本轮清理的商家数量
     */
    int purgeExpiredWithdrawals();
}
