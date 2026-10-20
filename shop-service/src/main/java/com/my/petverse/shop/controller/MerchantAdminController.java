package com.my.petverse.shop.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.shop.MerchantAdminPageQueryDTO;
import com.my.petverse.common.dto.shop.MerchantApplyAuditDTO;
import com.my.petverse.common.dto.shop.MerchantApplyPageQueryDTO;
import com.my.petverse.common.dto.shop.MerchantBanDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.MerchantAdminVO;
import com.my.petverse.common.vo.shop.MerchantApplyVO;
import com.my.petverse.shop.service.MerchantApplyService;
import com.my.petverse.shop.service.MerchantService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 商家管理接口控制器（管理端，网关已校验 ADMIN 角色）
 */
@RestController
@RequestMapping("/shop/admin")
@RequiredArgsConstructor
public class MerchantAdminController {

    private final MerchantApplyService merchantApplyService;

    private final MerchantService merchantService;

    /** 分页查询入驻申请（按状态/店铺名筛选，待审核置顶） */
    @GetMapping("/apply/page")
    public Result<PageResult<MerchantApplyVO>> page(@Valid MerchantApplyPageQueryDTO dto) {
        return Result.success(merchantApplyService.pageForAdmin(dto));
    }

    /** 审批入驻申请：通过则创建店铺并升级用户角色，驳回需填写原因 */
    @PostMapping("/apply/audit")
    public Result<Void> audit(@RequestBody @Valid MerchantApplyAuditDTO dto) {
        merchantApplyService.audit(UserContext.getUserId(), dto);
        return Result.success();
    }

    /** 分页查询商家店铺（按状态/撤销中/店铺名筛选） */
    @GetMapping("/merchant/page")
    public Result<PageResult<MerchantAdminVO>> merchantPage(@Valid MerchantAdminPageQueryDTO dto) {
        return Result.success(merchantService.pageForAdmin(dto));
    }

    /** 封禁商家：封禁后商城隐藏其商品且买家无法下单 */
    @PostMapping("/merchant/{id}/ban")
    public Result<Void> ban(@PathVariable("id") Long id, @RequestBody @Valid MerchantBanDTO dto) {
        merchantService.ban(id, dto);
        return Result.success();
    }

    /** 解封商家：解封后店铺恢复为打烊状态，由商家自行重新营业 */
    @PostMapping("/merchant/{id}/unban")
    public Result<Void> unban(@PathVariable("id") Long id) {
        merchantService.unban(id);
        return Result.success();
    }
}
