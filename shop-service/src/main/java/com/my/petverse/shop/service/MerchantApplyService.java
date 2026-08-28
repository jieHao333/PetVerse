package com.my.petverse.shop.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.shop.MerchantApplyAuditDTO;
import com.my.petverse.common.dto.shop.MerchantApplyPageQueryDTO;
import com.my.petverse.common.dto.shop.MerchantApplySaveDTO;
import com.my.petverse.common.dto.shop.MerchantApplyUpdateDTO;
import com.my.petverse.common.entity.shop.MerchantApply;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.shop.MerchantApplyVO;

/**
 * 商家入驻申请服务接口
 */
public interface MerchantApplyService extends IService<MerchantApply> {

    /**
     * 提交入驻申请，已有待审核申请或已是商家时抛业务异常
     *
     * @param userId 申请人用户ID
     * @param dto    申请参数
     * @return 申请单信息
     */
    MerchantApplyVO submit(Long userId, MerchantApplySaveDTO dto);

    /**
     * 驳回后修改并重新提交申请，仅申请人本人可操作
     *
     * @param userId 申请人用户ID
     * @param dto    重提参数
     * @return 申请单信息
     */
    MerchantApplyVO resubmit(Long userId, MerchantApplyUpdateDTO dto);

    /**
     * 查询当前用户最新的入驻申请
     *
     * @param userId 用户ID
     * @return 申请单信息，从未申请返回 null
     */
    MerchantApplyVO getMine(Long userId);

    /**
     * 管理员分页查询入驻申请（按状态/店铺名筛选），聚合申请人昵称
     *
     * @param dto 分页查询参数
     * @return 申请单分页结果
     */
    PageResult<MerchantApplyVO> pageForAdmin(MerchantApplyPageQueryDTO dto);

    /**
     * 管理员审批入驻申请：通过则创建商家店铺并升级用户角色，驳回需填写原因
     *
     * @param adminId 审批管理员ID
     * @param dto     审批参数
     */
    void audit(Long adminId, MerchantApplyAuditDTO dto);
}
