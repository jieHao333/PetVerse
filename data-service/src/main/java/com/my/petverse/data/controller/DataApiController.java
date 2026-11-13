package com.my.petverse.data.controller;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.data.ProductStatsVO;
import com.my.petverse.common.vo.data.ModelVO;
import com.my.petverse.common.vo.data.UserProfileVO;
import com.my.petverse.data.service.DataAssetService;
import com.my.petverse.data.service.DataModelService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 统一数据服务（Data API）内部接口，仅供 ai-service 等服务间调用。
 * 网关拦截外部 /internal/ 访问；调用方注入 X-User-Id 表明以哪个用户身份取数。
 * 一次返回治理好的画像/特征/商品统计，替代 AI 侧逐接口现拉。
 */
@RestController
@RequestMapping("/data/internal")
@RequiredArgsConstructor
public class DataApiController {

    private final DataAssetService assetService;
    private final DataModelService modelService;

    /** 当前激活的重排模型（供推荐打分；无模型时返回 null，AI 侧退化为启发式） */
    @GetMapping("/model")
    public Result<ModelVO> model() {
        return Result.success(modelService.getActiveModel());
    }

    /** 用户画像/特征（不存在时返回带默认值的空画像） */
    @GetMapping("/profile/{userId}")
    public Result<UserProfileVO> profile(@PathVariable("userId") Long userId) {
        return Result.success(assetService.getProfile(userId));
    }

    /** 批量商品统计（供推荐重排参考 CTR/热度） */
    @GetMapping("/product-stats")
    public Result<List<ProductStatsVO>> productStats(@RequestParam("ids") List<Long> ids) {
        return Result.success(assetService.listProductStats(ids));
    }
}