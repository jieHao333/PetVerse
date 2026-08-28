package com.my.petverse.shop.controller;

import com.my.petverse.common.dto.shop.MerchantApplySaveDTO;
import com.my.petverse.common.dto.shop.MerchantApplyUpdateDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.MerchantApplyVO;
import com.my.petverse.shop.service.MerchantApplyService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 商家入驻申请接口控制器（用户端）
 */
@RestController
@RequestMapping("/shop/apply")
@RequiredArgsConstructor
public class MerchantApplyController {

    private final MerchantApplyService merchantApplyService;

    /** 提交入驻申请（用户ID由网关注入的 X-User-Id 请求头提供） */
    @PostMapping
    public Result<MerchantApplyVO> submit(@RequestHeader("X-User-Id") Long userId,
                                          @RequestBody @Valid MerchantApplySaveDTO dto) {
        return Result.success(merchantApplyService.submit(userId, dto));
    }

    /** 驳回后修改并重新提交申请 */
    @PutMapping
    public Result<MerchantApplyVO> resubmit(@RequestHeader("X-User-Id") Long userId,
                                            @RequestBody @Valid MerchantApplyUpdateDTO dto) {
        return Result.success(merchantApplyService.resubmit(userId, dto));
    }

    /** 查询当前用户最新的入驻申请及审批结果 */
    @GetMapping("/mine")
    public Result<MerchantApplyVO> mine(@RequestHeader("X-User-Id") Long userId) {
        return Result.success(merchantApplyService.getMine(userId));
    }
}
