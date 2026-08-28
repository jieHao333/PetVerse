package com.my.petverse.shop.controller;

import com.my.petverse.common.dto.shop.MerchantApplyAuditDTO;
import com.my.petverse.common.dto.shop.MerchantApplyPageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.MerchantApplyVO;
import com.my.petverse.shop.service.MerchantApplyService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 商家入驻审批接口控制器（管理端，网关已校验 ADMIN 角色）
 */
@RestController
@RequestMapping("/shop/admin")
@RequiredArgsConstructor
public class MerchantAdminController {

    private final MerchantApplyService merchantApplyService;

    /** 分页查询入驻申请（按状态/店铺名筛选，待审核置顶） */
    @GetMapping("/apply/page")
    public Result<PageResult<MerchantApplyVO>> page(@Valid MerchantApplyPageQueryDTO dto) {
        return Result.success(merchantApplyService.pageForAdmin(dto));
    }

    /** 审批入驻申请：通过则创建店铺并升级用户角色，驳回需填写原因 */
    @PostMapping("/apply/audit")
    public Result<Void> audit(@RequestHeader("X-User-Id") Long adminId,
                              @RequestBody @Valid MerchantApplyAuditDTO dto) {
        merchantApplyService.audit(adminId, dto);
        return Result.success();
    }
}
