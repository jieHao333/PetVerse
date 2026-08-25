package com.my.petverse.social.service;

import com.my.petverse.common.dto.social.ChatMessageSendDTO;
import com.my.petverse.common.vo.social.ChatFileUploadVO;
import com.my.petverse.common.vo.social.ChatMessageVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * 聊天服务接口
 */
public interface ChatService {

    /** 发送消息，仅好友之间可发送 */
    ChatMessageVO sendMessage(Long senderId, ChatMessageSendDTO dto);

    /** 查询与某好友的最近聊天记录（按时间升序返回） */
    List<ChatMessageVO> listMessages(Long userId, Long friendUserId);

    /** 上传聊天文件（图片/文档/压缩包等）至 OSS，返回地址与消息类型 */
    ChatFileUploadVO uploadChatFile(MultipartFile file);
}
