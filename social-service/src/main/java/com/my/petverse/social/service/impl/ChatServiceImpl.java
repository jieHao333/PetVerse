package com.my.petverse.social.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.social.ChatMessageSendDTO;
import com.my.petverse.common.entity.social.ChatConversation;
import com.my.petverse.common.entity.social.ChatMessage;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.oss.OssService;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.social.ChatConversationVO;
import com.my.petverse.common.vo.social.ChatFileUploadVO;
import com.my.petverse.common.vo.social.ChatMessageVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.social.mapper.ChatConversationMapper;
import com.my.petverse.social.mapper.ChatMessageMapper;
import com.my.petverse.social.service.ChatService;
import com.my.petverse.social.service.FriendService;
import com.my.petverse.social.support.UserProfileLoader;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 聊天服务实现类
 */
@Service
@RequiredArgsConstructor
public class ChatServiceImpl extends ServiceImpl<ChatMessageMapper, ChatMessage> implements ChatService {

    /** 单次返回的最大消息条数 */
    private static final int MAX_MESSAGE_COUNT = 200;

    /** 消息类型：文本 / 图片 / 文件 */
    private static final int MSG_TEXT = 0;
    private static final int MSG_IMAGE = 1;
    private static final int MSG_FILE = 2;

    /** 图片类扩展名：命中则按图片消息内联展示，其余一律按文件消息 */
    private static final Set<String> IMAGE_EXTENSIONS = Set.of("jpg", "jpeg", "png", "webp", "gif");

    /** 单个聊天文件上限 20MB */
    private static final long MAX_FILE_SIZE = 20L * 1024 * 1024;

    /** 非好友在对方回复前可发送的打招呼消息条数 */
    private static final int STRANGER_QUOTA = 1;

    private final FriendService friendService;

    private final OssService ossService;

    private final ChatConversationMapper chatConversationMapper;

    private final UserProfileLoader userProfileLoader;

    /**
     * 发送消息：好友不限；非好友在对方回复前只能发 STRANGER_QUOTA 条打招呼消息，
     * 对方回复后即视为双向沟通成立，可继续聊天
     */
    @Override
    public ChatMessageVO sendMessage(Long senderId, ChatMessageSendDTO dto) {
        Long receiverId = dto.getReceiverId();
        if (receiverId.equals(senderId)) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "不能给自己发送消息");
        }
        checkStrangerQuota(senderId, receiverId);
        ChatMessage message = new ChatMessage();
        message.setSenderId(senderId);
        message.setReceiverId(receiverId);
        message.setContent(dto.getContent().trim());
        // 不传消息类型时默认文本，兼容存量前端调用；文件名仅图片/文件消息记录
        int msgType = dto.getMsgType() == null ? MSG_TEXT : dto.getMsgType();
        message.setMsgType(msgType);
        message.setFileName(msgType == MSG_TEXT ? null : dto.getFileName());
        save(message);
        return toVO(message);
    }

    /** 非好友额度校验：对方从未回复过时，我发出的消息达到额度即拒绝，提示先加好友 */
    private void checkStrangerQuota(Long senderId, Long receiverId) {
        if (friendService.isFriend(senderId, receiverId)) {
            return;
        }
        boolean receiverReplied = count(new LambdaQueryWrapper<ChatMessage>()
                .eq(ChatMessage::getSenderId, receiverId)
                .eq(ChatMessage::getReceiverId, senderId)) > 0;
        if (receiverReplied) {
            return;
        }
        long mySentCount = count(new LambdaQueryWrapper<ChatMessage>()
                .eq(ChatMessage::getSenderId, senderId)
                .eq(ChatMessage::getReceiverId, receiverId));
        if (mySentCount >= STRANGER_QUOTA) {
            throw new BusinessException(ResultCode.FORBIDDEN,
                    "你们还不是好友，对方回复前只能发送 " + STRANGER_QUOTA + " 条消息，添加好友后可继续聊天");
        }
    }

    /**
     * 查询与某个用户的最近聊天记录，按时间升序返回。
     * 不按好友关系拦截：非好友的打招呼会话同样要能被双方打开查看
     */
    @Override
    public List<ChatMessageVO> listMessages(Long userId, Long friendUserId) {
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

    /** 上传聊天文件至 OSS：图片(jpg/jpeg/png/webp/gif)按图片消息处理，其余格式统一按文件消息，上限 20MB */
    @Override
    public ChatFileUploadVO uploadChatFile(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "上传文件不能为空");
        }
        if (file.getSize() > MAX_FILE_SIZE) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "文件大小不能超过20MB");
        }
        String extension = getExtension(file.getOriginalFilename());
        int msgType = IMAGE_EXTENSIONS.contains(extension) ? MSG_IMAGE : MSG_FILE;
        String objectKey = "chat/" + LocalDate.now().format(DateTimeFormatter.BASIC_ISO_DATE)
                + "/" + UUID.randomUUID() + "." + extension;
        String url = ossService.upload(objectKey, file);
        ChatFileUploadVO vo = new ChatFileUploadVO();
        vo.setUrl(url);
        vo.setMsgType(msgType);
        vo.setFileName(file.getOriginalFilename());
        return vo;
    }

    /** 取文件扩展名（小写），无扩展名时用 bin 兑底避免生成非法 objectKey */
    private String getExtension(String filename) {
        if (!StringUtils.hasText(filename)) {
            return "bin";
        }
        int index = filename.lastIndexOf('.');
        if (index < 0 || index == filename.length() - 1) {
            return "bin";
        }
        return filename.substring(index + 1).toLowerCase(Locale.ROOT);
    }

    /** DO 转 VO */
    private ChatMessageVO toVO(ChatMessage message) {
        ChatMessageVO vo = new ChatMessageVO();
        BeanUtils.copyProperties(message, vo);
        return vo;
    }

    /**
     * 查询我的消息（会话）列表。
     * 会话对象取自消息表而非好友关系：非好友之间的打招呼也要能被收到方看到并回复，
     * 仅当存在未被清空(晚于 clear_time)的消息时才进入列表，
     * 未读数统计对方发给我且晚于 last_read_time 的消息数。结果按最后消息时间倒序。
     */
    @Override
    public List<ChatConversationVO> listConversations(Long userId) {
        List<Long> partnerIds = listPartnerIds(userId);
        if (partnerIds.isEmpty()) {
            return new ArrayList<>();
        }
        Set<Long> friendIds = Set.copyOf(friendService.listFriendIds(userId));
        Map<Long, UserVO> users = userProfileLoader.loadQuietly(partnerIds);
        List<ChatConversationVO> result = new ArrayList<>();
        for (Long partnerId : partnerIds) {
            ChatConversation conversation = findConversation(userId, partnerId);
            LocalDateTime clearTime = conversation == null ? null : conversation.getClearTime();
            LocalDateTime lastReadTime = conversation == null ? null : conversation.getLastReadTime();
            // 未被清空部分里的最后一条消息：决定该会话是否展示及预览内容
            ChatMessage lastMessage = getOne(new LambdaQueryWrapper<ChatMessage>()
                    .and(w -> w
                            .and(w1 -> w1.eq(ChatMessage::getSenderId, userId)
                                    .eq(ChatMessage::getReceiverId, partnerId))
                            .or(w1 -> w1.eq(ChatMessage::getSenderId, partnerId)
                                    .eq(ChatMessage::getReceiverId, userId)))
                    .gt(clearTime != null, ChatMessage::getCreateTime, clearTime)
                    .orderByDesc(ChatMessage::getCreateTime)
                    .orderByDesc(ChatMessage::getId)
                    .last("limit 1"));
            if (lastMessage == null) {
                continue;
            }
            // 未读数：对方发给我、晚于最后已读时间的消息数
            long unreadCount = count(new LambdaQueryWrapper<ChatMessage>()
                    .eq(ChatMessage::getSenderId, partnerId)
                    .eq(ChatMessage::getReceiverId, userId)
                    .gt(lastReadTime != null, ChatMessage::getCreateTime, lastReadTime));
            UserVO partner = users.get(partnerId);
            ChatConversationVO vo = new ChatConversationVO();
            vo.setFriendUserId(partnerId);
            if (partner != null) {
                vo.setUsername(partner.getUsername());
                vo.setNickname(partner.getNickname());
                vo.setAvatar(partner.getAvatar());
            } else {
                // 用户服务不可用或用户已注销时，用库内ID降级展示，保证会话不丢失
                vo.setUsername("用户" + partnerId);
                vo.setNickname("未知用户");
            }
            vo.setFriend(friendIds.contains(partnerId));
            vo.setLastContent(lastMessage.getContent());
            vo.setLastMsgType(lastMessage.getMsgType());
            vo.setLastTime(lastMessage.getCreateTime());
            vo.setUnreadCount(unreadCount);
            result.add(vo);
        }
        result.sort(Comparator.comparing(ChatConversationVO::getLastTime,
                Comparator.nullsLast(Comparator.reverseOrder())));
        return result;
    }

    /** 与我有过消息往来的对方用户ID：消息表双向去重，非好友的往来对象也要进入会话列表 */
    private List<Long> listPartnerIds(Long userId) {
        Set<Long> partnerIds = new LinkedHashSet<>();
        partnerIds.addAll(list(new LambdaQueryWrapper<ChatMessage>()
                        .select(ChatMessage::getSenderId)
                        .eq(ChatMessage::getReceiverId, userId)
                        .groupBy(ChatMessage::getSenderId))
                .stream().map(ChatMessage::getSenderId).collect(Collectors.toSet()));
        partnerIds.addAll(list(new LambdaQueryWrapper<ChatMessage>()
                        .select(ChatMessage::getReceiverId)
                        .eq(ChatMessage::getSenderId, userId)
                        .groupBy(ChatMessage::getReceiverId))
                .stream().map(ChatMessage::getReceiverId).collect(Collectors.toSet()));
        return new ArrayList<>(partnerIds);
    }

    /** 标记与某好友的会话为已读：将最后已读时间推进到现在，清零未读角标 */
    @Override
    public void markRead(Long userId, Long friendUserId) {
        ChatConversation conversation = upsertConversation(userId, friendUserId);
        conversation.setLastReadTime(LocalDateTime.now());
        chatConversationMapper.updateById(conversation);
    }

    /** 删除（清空）会话：记录清空时间并推进已读时间，使会话从消息列表移除且不计未读 */
    @Override
    public void deleteConversation(Long userId, Long friendUserId) {
        ChatConversation conversation = upsertConversation(userId, friendUserId);
        LocalDateTime now = LocalDateTime.now();
        conversation.setClearTime(now);
        conversation.setLastReadTime(now);
        chatConversationMapper.updateById(conversation);
    }

    /** 查询会话记录，不存在返回 null */
    private ChatConversation findConversation(Long userId, Long friendUserId) {
        return chatConversationMapper.selectOne(new LambdaQueryWrapper<ChatConversation>()
                .eq(ChatConversation::getUserId, userId)
                .eq(ChatConversation::getFriendUserId, friendUserId));
    }

    /** 获取会话记录，不存在则新建并落库（用于已读/清空标记） */
    private ChatConversation upsertConversation(Long userId, Long friendUserId) {
        ChatConversation conversation = findConversation(userId, friendUserId);
        if (conversation == null) {
            conversation = new ChatConversation();
            conversation.setUserId(userId);
            conversation.setFriendUserId(friendUserId);
            chatConversationMapper.insert(conversation);
        }
        return conversation;
    }
}
