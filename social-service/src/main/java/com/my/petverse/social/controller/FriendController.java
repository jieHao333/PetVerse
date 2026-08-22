package com.my.petverse.social.controller;

import com.my.petverse.common.dto.social.FriendRequestSendDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.social.FriendRequestVO;
import com.my.petverse.common.vo.social.FriendVO;
import com.my.petverse.social.service.FriendService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 好友接口控制器（当前用户ID由网关注入的 X-User-Id 请求头提供）
 * 路径以 /social 开头，与网关 /api/social/** 路由剥离 /api 后的路径对应
 */
@RestController
@RequestMapping("/social/friend")
@RequiredArgsConstructor
public class FriendController {

    private final FriendService friendService;

    /** 发起好友申请 */
    @PostMapping("/request")
    public Result<Boolean> sendRequest(@RequestHeader("X-User-Id") Long userId,
                                       @RequestBody @Valid FriendRequestSendDTO dto) {
        return Result.success(friendService.sendRequest(userId, dto));
    }

    /** 查询我收到的好友申请 */
    @GetMapping("/request/received")
    public Result<List<FriendRequestVO>> receivedRequests(@RequestHeader("X-User-Id") Long userId) {
        return Result.success(friendService.listMyRequests(userId));
    }

    /** 同意好友申请 */
    @PutMapping("/request/{id}/accept")
    public Result<Boolean> accept(@RequestHeader("X-User-Id") Long userId,
                                  @PathVariable("id") Long id) {
        return Result.success(friendService.acceptRequest(userId, id));
    }

    /** 拒绝好友申请 */
    @PutMapping("/request/{id}/reject")
    public Result<Boolean> reject(@RequestHeader("X-User-Id") Long userId,
                                  @PathVariable("id") Long id) {
        return Result.success(friendService.rejectRequest(userId, id));
    }

    /** 查询我的好友列表 */
    @GetMapping("/list")
    public Result<List<FriendVO>> listFriends(@RequestHeader("X-User-Id") Long userId) {
        return Result.success(friendService.listFriends(userId));
    }

    /** 删除好友 */
    @DeleteMapping("/{friendUserId}")
    public Result<Boolean> removeFriend(@RequestHeader("X-User-Id") Long userId,
                                        @PathVariable("friendUserId") Long friendUserId) {
        return Result.success(friendService.removeFriend(userId, friendUserId));
    }
}
