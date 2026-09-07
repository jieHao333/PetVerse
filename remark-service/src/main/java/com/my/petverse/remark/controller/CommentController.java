package com.my.petverse.remark.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.remark.CommentPageQueryDTO;
import com.my.petverse.common.dto.remark.CommentSaveDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.remark.CommentVO;
import com.my.petverse.remark.service.CommentService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 通用评论接口控制器，对象以 (targetType, targetId) 定位，当前用于圈子动态评论
 */
@RestController
@RequestMapping("/remark/comment")
@RequiredArgsConstructor
public class CommentController {

    private final CommentService commentService;

    /** 发表评论（支持回复某条评论，所有登录用户均可评论） */
    @PostMapping
    public Result<CommentVO> save(@Valid @RequestBody CommentSaveDTO dto) {
        return Result.success(commentService.saveComment(UserContext.getUserId(), dto));
    }

    /** 分页查询指定对象的评论（按时间正序展示互动过程） */
    @GetMapping("/page")
    public Result<PageResult<CommentVO>> page(@Valid CommentPageQueryDTO dto) {
        return Result.success(commentService.pageComments(dto));
    }

    /** 删除评论（仅评论人本人可删除，逻辑删除） */
    @DeleteMapping("/{id}")
    public Result<Boolean> delete(@PathVariable("id") Long id) {
        return Result.success(commentService.deleteComment(UserContext.getUserId(), id));
    }

    /** 批量查询评论数（仅计数），供圈子列表页展示每条动态的评论数，targetIds 逗号分隔 */
    @GetMapping("/counts")
    public Result<Map<Long, Long>> counts(@RequestParam("targetType") Integer targetType,
                                          @RequestParam("targetIds") String targetIds) {
        return Result.success(commentService.batchCounts(targetType, parseIds(targetIds)));
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
