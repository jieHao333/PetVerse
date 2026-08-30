package com.my.petverse.remark.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.remark.LikeBatchVO;
import com.my.petverse.remark.service.LikeService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 通用点赞接口控制器，对象以 (targetType, targetId) 定位
 */
@RestController
@RequestMapping("/remark/like")
@RequiredArgsConstructor
public class LikeController {

    private final LikeService likeService;

    /** 点赞（幂等，重复点赞返回成功） */
    @PostMapping("/{targetType}/{targetId}")
    public Result<Boolean> like(@PathVariable("targetType") Integer targetType,
                                @PathVariable("targetId") Long targetId) {
        likeService.like(targetType, targetId, UserContext.getUserId());
        return Result.success(true);
    }

    /** 取消点赞（幂等） */
    @DeleteMapping("/{targetType}/{targetId}")
    public Result<Boolean> unlike(@PathVariable("targetType") Integer targetType,
                                  @PathVariable("targetId") Long targetId) {
        likeService.unlike(targetType, targetId, UserContext.getUserId());
        return Result.success(true);
    }

    /** 批量查询点赞数与当前用户是否点赞，targetIds 逗号分隔 */
    @GetMapping("/batch")
    public Result<LikeBatchVO> batch(@RequestParam("targetType") Integer targetType,
                                     @RequestParam("targetIds") String targetIds) {
        return Result.success(likeService.batchQuery(targetType, parseIds(targetIds), UserContext.getUserId()));
    }

    /** 批量查询点赞数（仅计数），供其他服务内部 Feign 调用 */
    @GetMapping("/internal/counts")
    public Result<Map<Long, Long>> counts(@RequestParam("targetType") Integer targetType,
                                          @RequestParam("targetIds") String targetIds) {
        return Result.success(likeService.batchCounts(targetType, parseIds(targetIds)));
    }

    /** 解析逗号分隔的ID列表 */
    private List<Long> parseIds(String targetIds) {
        return Arrays.stream(targetIds.split(","))
                .map(String::trim)
                .filter(s -> !s.isEmpty())
                .map(Long::valueOf)
                .collect(Collectors.toList());
    }
}
