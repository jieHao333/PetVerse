package com.my.petverse.data.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.dto.data.DataEventQueryDTO;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.data.BiOverviewVO;
import com.my.petverse.common.vo.data.DataEventVO;
import com.my.petverse.common.vo.data.MetricDailyVO;
import com.my.petverse.common.vo.data.ProductStatsVO;
import com.my.petverse.common.vo.data.ModelVO;
import com.my.petverse.common.vo.data.UserProfileVO;
import com.my.petverse.data.service.DataAssetService;
import com.my.petverse.data.service.DataCollectService;
import com.my.petverse.data.service.DataMetricService;
import com.my.petverse.data.service.DataModelService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.List;

/**
 * 数据中台管理端 BI 接口，仅管理员可访问（网关角色校验 + 此处二次校验，纵深防御）。
 */
@RestController
@RequestMapping("/data/admin")
@RequiredArgsConstructor
public class DataBiController {

    private final DataMetricService metricService;
    private final DataAssetService assetService;
    private final DataCollectService collectService;
    private final DataModelService modelService;

    /** BI 概览：资产总量 + 当日指标 + 热门商品 */
    @GetMapping("/overview")
    public Result<BiOverviewVO> overview() {
        requireAdmin();
        return Result.success(metricService.overview());
    }

    /** 指标趋势（默认最近7天） */
    @GetMapping("/metrics")
    public Result<List<MetricDailyVO>> metrics(@RequestParam(value = "start", required = false) String start,
                                               @RequestParam(value = "end", required = false) String end) {
        requireAdmin();
        LocalDate endDate = parseDate(end, LocalDate.now());
        LocalDate startDate = parseDate(start, endDate.minusDays(6));
        return Result.success(metricService.listMetrics(startDate, endDate));
    }

    /** 商品统计分页（按热度降序） */
    @GetMapping("/products")
    public Result<PageResult<ProductStatsVO>> products(@Valid BasePageQuery query) {
        requireAdmin();
        return Result.success(assetService.pageProductStats(query));
    }

    /** 用户画像分页 */
    @GetMapping("/profiles")
    public Result<PageResult<UserProfileVO>> profiles(@Valid BasePageQuery query) {
        requireAdmin();
        return Result.success(assetService.pageProfiles(query));
    }

    /** 原始事件分页（治理排查） */
    @GetMapping("/events")
    public Result<PageResult<DataEventVO>> events(@Valid DataEventQueryDTO query) {
        requireAdmin();
        return Result.success(collectService.pageEvents(query));
    }

    /** 手动触发一次聚合（便于演示与运维） */
    @PostMapping("/aggregate")
    public Result<Boolean> aggregate() {
        requireAdmin();
        assetService.aggregate();
        metricService.aggregate(LocalDate.now());
        return Result.success(true);
    }

    /** 模型注册表列表 */
    @GetMapping("/models")
    public Result<List<ModelVO>> models() {
        requireAdmin();
        return Result.success(modelService.listModels());
    }

    /** 手动触发模型训练（样本不足时返回业务错误提示） */
    @PostMapping("/train")
    public Result<ModelVO> train() {
        requireAdmin();
        return Result.success(modelService.train());
    }

    private void requireAdmin() {
        if (!"ADMIN".equals(UserContext.getRole())) {
            throw new BusinessException(ResultCode.FORBIDDEN, "仅管理员可访问数据看板");
        }
    }

    private LocalDate parseDate(String s, LocalDate def) {
        if (s == null || s.isBlank()) {
            return def;
        }
        try {
            return LocalDate.parse(s);
        } catch (Exception e) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "日期格式应为 yyyy-MM-dd");
        }
    }
}