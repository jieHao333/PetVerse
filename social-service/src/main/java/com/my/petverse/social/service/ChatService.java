package com.my.petverse.social.service;

import com.my.petverse.common.dto.social.ChatMessageSendDTO;
import com.my.petverse.common.vo.social.ChatConversationVO;
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

    /** 查询我的消息（会话）列表：仅返回有聊天记录且未被清空的好友，按最后消息时间倒序 */
    List<ChatConversationVO> listConversations(Long userId);

    /** 标记与某好友的会话为已读（进入聊天时调用，清零未读角标） */
    void markRead(Long userId, Long friendUserId);

    /** 删除（清空）与某好友的会话：从消息列表移除，好友列表中可重新发起聊天 */
    void deleteConversation(Long userId, Long friendUserId);
}
