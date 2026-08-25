package com.my.petverse.social.controller;

import com.my.petverse.common.dto.social.ChatMessageSendDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.social.ChatFileUploadVO;
import com.my.petverse.common.vo.social.ChatMessageVO;
import com.my.petverse.social.service.ChatService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * 聊天接口控制器（当前用户ID由网关注入的 X-User-Id 请求头提供）
 * 路径以 /social 开头，与网关 /api/social/** 路由剥离 /api 后的路径对应
 */
@RestController
@RequestMapping("/social/chat")
@RequiredArgsConstructor
public class ChatController {

    private final ChatService chatService;

    /** 发送消息 */
    @PostMapping("/message")
    public Result<ChatMessageVO> sendMessage(@RequestHeader("X-User-Id") Long userId,
                                             @RequestBody @Valid ChatMessageSendDTO dto) {
        return Result.success(chatService.sendMessage(userId, dto));
    }

    /** 查询与某好友的最近聊天记录 */
    @GetMapping("/messages")
    public Result<List<ChatMessageVO>> messages(@RequestHeader("X-User-Id") Long userId,
                                                @RequestParam("friendUserId") Long friendUserId) {
        return Result.success(chatService.listMessages(userId, friendUserId));
    }

    /** 上传聊天文件（图片/文档/压缩包等，上限 20MB），返回 OSS 地址与消息类型 */
    @PostMapping("/file")
    public Result<ChatFileUploadVO> uploadFile(@RequestParam("file") MultipartFile file) {
        return Result.success(chatService.uploadChatFile(file));
    }
}
