package com.my.petverse.remark.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.remark.NotificationVO;
import com.my.petverse.remark.service.NotificationService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 站内通知接口控制器：查询我的通知、未读数、标记已读
 */
@RestController
@RequestMapping("/remark/notification")
@RequiredArgsConstructor
public class NotificationController {

    private final NotificationService notificationService;

    /** 分页查询我的通知（时间倒序），onlyUnread=true 仅看未读 */
    @GetMapping("/page")
    public Result<PageResult<NotificationVO>> page(@Valid BasePageQuery query,
                                                   @RequestParam(value = "onlyUnread", required = false) Boolean onlyUnread) {
        return Result.success(notificationService.pageNotifications(UserContext.getUserId(), onlyUnread, query));
    }

    /** 我的未读通知数（顶栏铃铛轮询） */
    @GetMapping("/unread-count")
    public Result<Long> unreadCount() {
        return Result.success(notificationService.unreadCount(UserContext.getUserId()));
    }

    /** 标记单条通知为已读 */
    @PutMapping("/{id}/read")
    public Result<Boolean> markRead(@PathVariable("id") Long id) {
        return Result.success(notificationService.markRead(UserContext.getUserId(), id));
    }

    /** 标记我的全部通知为已读 */
    @PutMapping("/read-all")
    public Result<Integer> markAllRead() {
        return Result.success(notificationService.markAllRead(UserContext.getUserId()));
    }
}
