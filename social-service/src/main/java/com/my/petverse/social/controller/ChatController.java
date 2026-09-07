package com.my.petverse.social.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.social.ChatMessageSendDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.social.ChatConversationVO;
import com.my.petverse.common.vo.social.ChatFileUploadVO;
import com.my.petverse.common.vo.social.ChatMessageVO;
import com.my.petverse.social.service.ChatService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * 聊天接口控制器（当前用户ID由登录令牌解析写入的 UserContext 提供）
 * 路径以 /social 开头，与网关 /api/social/** 路由剥离 /api 后的路径对应
 */
@RestController
@RequestMapping("/social/chat")
@RequiredArgsConstructor
public class ChatController {

    private final ChatService chatService;

    /** 发送消息 */
    @PostMapping("/message")
    public Result<ChatMessageVO> sendMessage(@RequestBody @Valid ChatMessageSendDTO dto) {
        return Result.success(chatService.sendMessage(UserContext.getUserId(), dto));
    }

    /** 查询与某好友的最近聊天记录 */
    @GetMapping("/messages")
    public Result<List<ChatMessageVO>> messages(@RequestParam("friendUserId") Long friendUserId) {
        return Result.success(chatService.listMessages(UserContext.getUserId(), friendUserId));
    }

    /** 上传聊天文件（图片/文档/压缩包等，上限 20MB），返回 OSS 地址与消息类型 */
    @PostMapping("/file")
    public Result<ChatFileUploadVO> uploadFile(@RequestParam("file") MultipartFile file) {
        return Result.success(chatService.uploadChatFile(file));
    }

    /** 查询我的消息（会话）列表 */
    @GetMapping("/conversations")
    public Result<List<ChatConversationVO>> conversations() {
        return Result.success(chatService.listConversations(UserContext.getUserId()));
    }

    /** 标记与某好友的会话为已读 */
    @PostMapping("/read")
    public Result<Void> markRead(@RequestParam("friendUserId") Long friendUserId) {
        chatService.markRead(UserContext.getUserId(), friendUserId);
        return Result.success(null);
    }

    /** 删除（清空）与某好友的会话 */
    @DeleteMapping("/conversation")
    public Result<Void> deleteConversation(@RequestParam("friendUserId") Long friendUserId) {
        chatService.deleteConversation(UserContext.getUserId(), friendUserId);
        return Result.success(null);
    }
}
