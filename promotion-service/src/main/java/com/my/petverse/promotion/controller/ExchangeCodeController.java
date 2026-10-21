package com.my.petverse.promotion.controller;

import com.my.petverse.common.dto.promotion.CodePageQueryDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.promotion.ExchangeCodeVO;
import com.my.petverse.promotion.service.ExchangeCodeService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 兑换码接口控制器（管理端，网关校验 /api/promotion/admin 的 ADMIN 角色）
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/promotion/admin/coupon-codes")
public class ExchangeCodeController {

    private final ExchangeCodeService codeService;

    /** 分页查询兑换码 */
    @GetMapping("/page")
    public Result<PageResult<ExchangeCodeVO>> queryCodePage(@Valid CodePageQueryDTO query) {
        return Result.success(codeService.queryCodePage(query));
    }
}
