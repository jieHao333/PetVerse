package com.my.petverse.social.service;

import com.my.petverse.common.dto.social.ChatMessageSendDTO;
import com.my.petverse.common.vo.social.ChatMessageVO;

import java.util.List;

/**
 * 聊天服务接口
 */
public interface ChatService {

    /** 发送消息，仅好友之间可发送 */
    ChatMessageVO sendMessage(Long senderId, ChatMessageSendDTO dto);

    /** 查询与某好友的最近聊天记录（按时间升序返回） */
    List<ChatMessageVO> listMessages(Long userId, Long friendUserId);
}
