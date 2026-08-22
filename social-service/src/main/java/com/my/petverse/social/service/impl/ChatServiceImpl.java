package com.my.petverse.social.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.social.ChatMessageSendDTO;
import com.my.petverse.common.entity.social.ChatMessage;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.social.ChatMessageVO;
import com.my.petverse.social.mapper.ChatMessageMapper;
import com.my.petverse.social.service.ChatService;
import com.my.petverse.social.service.FriendService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;

import java.util.Collections;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 聊天服务实现类
 */
@Service
@RequiredArgsConstructor
public class ChatServiceImpl extends ServiceImpl<ChatMessageMapper, ChatMessage> implements ChatService {

    /** 单次返回的最大消息条数 */
    private static final int MAX_MESSAGE_COUNT = 200;

    private final FriendService friendService;

    /** 发送消息，仅好友之间可发送 */
    @Override
    public ChatMessageVO sendMessage(Long senderId, ChatMessageSendDTO dto) {
        if (dto.getReceiverId().equals(senderId)) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "不能给自己发送消息");
        }
        if (!friendService.isFriend(senderId, dto.getReceiverId())) {
            throw new BusinessException(ResultCode.FORBIDDEN, "你们还不是好友，先添加好友再聊天吧");
        }
        ChatMessage message = new ChatMessage();
        message.setSenderId(senderId);
        message.setReceiverId(dto.getReceiverId());
        message.setContent(dto.getContent().trim());
        save(message);
        return toVO(message);
    }

    /** 查询与某好友的最近聊天记录，按时间升序返回 */
    @Override
    public List<ChatMessageVO> listMessages(Long userId, Long friendUserId) {
        if (!friendService.isFriend(userId, friendUserId)) {
            throw new BusinessException(ResultCode.FORBIDDEN, "你们还不是好友，先添加好友再聊天吧");
        }
        // 双向匹配消息，取最近若干条后反转为时间升序，便于前端从上到下渲染
        List<ChatMessage> messages = list(new LambdaQueryWrapper<ChatMessage>()
                .and(w -> w
                        .and(w1 -> w1.eq(ChatMessage::getSenderId, userId)
                                .eq(ChatMessage::getReceiverId, friendUserId))
                        .or(w1 -> w1.eq(ChatMessage::getSenderId, friendUserId)
                                .eq(ChatMessage::getReceiverId, userId)))
                .orderByDesc(ChatMessage::getCreateTime)
                .last("limit " + MAX_MESSAGE_COUNT));
        Collections.reverse(messages);
        return messages.stream().map(this::toVO).collect(Collectors.toList());
    }

    /** DO 转 VO */
    private ChatMessageVO toVO(ChatMessage message) {
        ChatMessageVO vo = new ChatMessageVO();
        BeanUtils.copyProperties(message, vo);
        return vo;
    }
}
