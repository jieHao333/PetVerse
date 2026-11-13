package com.my.petverse.data.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.data.TrackBatchDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.data.service.DataCollectService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 行为埋点接入控制器。前端 SDK 批量上报曝光/点击/搜索/AI反馈/页面浏览事件。
 */
@RestController
@RequestMapping("/data/track")
@RequiredArgsConstructor
public class DataTrackController {

    private final DataCollectService collectService;

    /** 批量上报行为事件，返回受理条数 */
    @PostMapping
    public Result<Integer> track(@Valid @RequestBody TrackBatchDTO dto) {
        return Result.success(collectService.ingestBehavior(UserContext.peekUserId(), dto.getEvents()));
    }
}