package com.my.petverse.remark.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.entity.remark.Notification;
import com.my.petverse.common.enums.NotificationSource;
import com.my.petverse.common.enums.NotificationType;
import com.my.petverse.common.mq.message.NotifyMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.remark.NotificationVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.remark.feign.UserFeignClient;
import com.my.petverse.remark.mapper.NotificationMapper;
import com.my.petverse.remark.service.NotificationService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 站内通知服务实现：
 * 通知由事件驱动落库，查询时通过 Feign 聚合触发人昵称头像（用户服务不可用时降级为不展示）
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NotificationServiceImpl extends ServiceImpl<NotificationMapper, Notification>
        implements NotificationService {

    private final UserFeignClient userFeignClient;

    @Override
    public boolean saveFromMessage(NotifyMessage message) {
        if (message == null || message.getRecipientUserId() == null) {
            return false;
        }
        Notification notification = new Notification();
        notification.setUserId(message.getRecipientUserId());
        notification.setActorUserId(message.getActorUserId());
        notification.setType(message.getType());
        notification.setSource(message.getSource());
        notification.setTargetId(message.getTargetId());
        notification.setContent(message.getContent());
        notification.setIsRead(0);
        return save(notification);
    }

    @Override
    public PageResult<NotificationVO> pageNotifications(Long userId, Boolean onlyUnread, BasePageQuery pageQuery) {
        Page<Notification> page = page(new Page<>(pageQuery.getPageNum(), pageQuery.getPageSize()),
                new LambdaQueryWrapper<Notification>()
                        .eq(Notification::getUserId, userId)
                        .eq(Boolean.TRUE.equals(onlyUnread), Notification::getIsRead, 0)
                        .orderByDesc(Notification::getCreateTime));
        List<Notification> notifications = page.getRecords();
        if (notifications.isEmpty()) {
            return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), List.of());
        }
        // 触发人去重后批量聚合昵称头像，避免同一用户重复调用用户服务
        Set<Long> actorIds = notifications.stream().map(Notification::getActorUserId)
                .filter(Objects::nonNull).collect(Collectors.toSet());
        Map<Long, UserVO> users = loadUserInfos(actorIds);
        List<NotificationVO> vos = notifications.stream()
                .map(notification -> toVO(notification, users.get(notification.getActorUserId())))
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    public long unreadCount(Long userId) {
        Long count = baseMapper.selectCount(new LambdaQueryWrapper<Notification>()
                .eq(Notification::getUserId, userId)
                .eq(Notification::getIsRead, 0));
        return count == null ? 0L : count;
    }

    @Override
    public boolean markRead(Long userId, Long id) {
        // 附带 userId 条件，确保只能标记本人的通知为已读，防止越权
        return update(new LambdaUpdateWrapper<Notification>()
                .eq(Notification::getId, id)
                .eq(Notification::getUserId, userId)
                .set(Notification::getIsRead, 1));
    }

    @Override
    public int markAllRead(Long userId) {
        // update 返回布尔而非影响行数，先查未读数作为返回值
        long unread = unreadCount(userId);
        if (unread == 0) {
            return 0;
        }
        update(new LambdaUpdateWrapper<Notification>()
                .eq(Notification::getUserId, userId)
                .eq(Notification::getIsRead, 0)
                .set(Notification::getIsRead, 1));
        return (int) unread;
    }

    /** Feign 查询用户信息，用户服务不可用时降级返回 null */
    private UserVO loadUserInfo(Long userId) {
        if (userId == null) {
            return null;
        }
        try {
            Result<UserVO> result = userFeignClient.getUserById(userId);
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode()) {
                return result.getData();
            }
        } catch (Exception e) {
            log.warn("查询通知触发人信息失败 userId={}", userId, e);
        }
        return null;
    }

    /** 批量查询用户信息（去重 + 逐个 Feign + 降级容错） */
    private Map<Long, UserVO> loadUserInfos(Collection<Long> userIds) {
        Map<Long, UserVO> users = new HashMap<>();
        for (Long userId : new HashSet<>(userIds)) {
            if (userId != null) {
                users.put(userId, loadUserInfo(userId));
            }
        }
        return users;
    }

    /** DO 转 VO，附加类型/来源展示文案与触发人昵称头像 */
    private NotificationVO toVO(Notification notification, UserVO actor) {
        NotificationVO vo = new NotificationVO();
        vo.setId(notification.getId());
        vo.setType(notification.getType());
        NotificationType type = NotificationType.of(notification.getType());
        vo.setTypeText(type == null ? null : type.getDesc());
        vo.setSource(notification.getSource());
        NotificationSource source = NotificationSource.of(notification.getSource());
        vo.setSourceText(source == null ? null : source.getDesc());
        vo.setActorUserId(notification.getActorUserId());
        vo.setContent(notification.getContent());
        vo.setTargetId(notification.getTargetId());
        vo.setIsRead(notification.getIsRead());
        vo.setCreateTime(notification.getCreateTime());
        if (actor != null) {
            vo.setActorNickname(actor.getNickname());
            vo.setActorAvatar(actor.getAvatar());
        }
        return vo;
    }
}
