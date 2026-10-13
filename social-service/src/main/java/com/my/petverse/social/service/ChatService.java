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

    /** 发送消息：好友不限；非好友在对方回复前只能发一条打招呼消息，超出即提示先加好友 */
    ChatMessageVO sendMessage(Long senderId, ChatMessageSendDTO dto);

    /** 查询与某用户的最近聊天记录（按时间升序返回，非好友的打招呼会话同样可读） */
    List<ChatMessageVO> listMessages(Long userId, Long friendUserId);

    /** 上传聊天文件（图片/文档/压缩包等）至 OSS，返回地址与消息类型 */
    ChatFileUploadVO uploadChatFile(MultipartFile file);

    /** 查询我的消息（会话）列表：返回有未被清空聊天记录的往来对象（含非好友），按最后消息时间倒序 */
    List<ChatConversationVO> listConversations(Long userId);

    /** 标记与某用户的会话为已读（进入聊天时调用，清零未读角标） */
    void markRead(Long userId, Long friendUserId);

    /** 删除（清空）与某用户的会话：从消息列表移除，好友或再次打招呼后可重新建立会话 */
    void deleteConversation(Long userId, Long friendUserId);
}
