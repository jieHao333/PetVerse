package com.my.petverse.remark.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.remark.CommentPageQueryDTO;
import com.my.petverse.common.dto.remark.CommentSaveDTO;
import com.my.petverse.common.entity.remark.Comment;
import com.my.petverse.common.enums.CommentTargetType;
import com.my.petverse.common.enums.NotificationSource;
import com.my.petverse.common.enums.NotificationType;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.NotifyMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.remark.CommentVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.remark.feign.UserFeignClient;
import com.my.petverse.remark.mapper.CommentMapper;
import com.my.petverse.remark.service.CommentService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;

import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 通用评论服务实现：
 * 纯 DB 存储（逻辑删除），评论人与被回复人昵称头像通过 Feign 聚合，
 * 用户服务不可用时降级为不展示；对象以 (targetType, targetId) 定位，当前用于圈子动态
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class CommentServiceImpl extends ServiceImpl<CommentMapper, Comment> implements CommentService {

    private final UserFeignClient userFeignClient;

    private final MqEventPublisher mqEventPublisher;

    @Override
    public CommentVO saveComment(Long userId, CommentSaveDTO dto) {
        checkTargetType(dto.getTargetType());
        Comment comment = new Comment();
        comment.setTargetType(dto.getTargetType());
        comment.setTargetId(dto.getTargetId());
        comment.setUserId(userId);
        comment.setReplyUserId(dto.getReplyUserId());
        comment.setContent(dto.getContent());
        save(comment);
        // 回复他人评论时通知被回复人（自己回复自己不通知），去重键用评论ID防重复投递
        if (dto.getReplyUserId() != null && !dto.getReplyUserId().equals(userId)) {
            NotifyMessage notify = new NotifyMessage(dto.getReplyUserId(), userId,
                    NotificationType.REPLY.getCode(), NotificationSource.SPACE.getCode(),
                    dto.getTargetId(), dto.getContent());
            mqEventPublisher.publish(MqTopics.NOTIFY, MqTopics.TAG_NOTIFY_CREATED,
                    notify, String.valueOf(comment.getId()));
        }
        // 一次查询同时覆盖评论人与被回复人的昵称头像
        Set<Long> userIds = new HashSet<>();
        userIds.add(userId);
        if (dto.getReplyUserId() != null) {
            userIds.add(dto.getReplyUserId());
        }
        return toVO(comment, loadUserInfos(userIds));
    }

    @Override
    public PageResult<CommentVO> pageComments(CommentPageQueryDTO dto) {
        checkTargetType(dto.getTargetType());
        Page<Comment> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<Comment>()
                        .eq(Comment::getTargetType, dto.getTargetType())
                        .eq(Comment::getTargetId, dto.getTargetId())
                        // 按时间正序展示互动过程
                        .orderByAsc(Comment::getCreateTime));
        List<Comment> comments = page.getRecords();
        if (comments.isEmpty()) {
            return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), List.of());
        }
        // 评论人与被回复人去重后批量聚合昵称头像，避免同一用户重复调用用户服务
        Set<Long> userIds = new HashSet<>();
        comments.forEach(comment -> {
            userIds.add(comment.getUserId());
            if (comment.getReplyUserId() != null) {
                userIds.add(comment.getReplyUserId());
            }
        });
        Map<Long, UserVO> users = loadUserInfos(userIds);
        List<CommentVO> vos = comments.stream()
                .map(comment -> toVO(comment, users))
                .collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    public boolean deleteComment(Long userId, Long commentId) {
        Comment comment = getById(commentId);
        // 仅评论人本人可删除，防止越权删除他人评论
        if (comment == null || !Objects.equals(comment.getUserId(), userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "评论不存在");
        }
        return removeById(commentId);
    }

    @Override
    public Map<Long, Long> batchCounts(Integer targetType, List<Long> targetIds) {
        checkTargetType(targetType);
        if (targetIds == null || targetIds.isEmpty()) {
            return Map.of();
        }
        // 一条 group by SQL 统计每个对象的评论数（逻辑删除条件由 MyBatis-Plus 自动追加）
        return baseMapper.selectMaps(new QueryWrapper<Comment>()
                        .select("target_id AS targetId", "COUNT(*) AS cnt")
                        .eq("target_type", targetType)
                        .in("target_id", targetIds)
                        .groupBy("target_id"))
                .stream().collect(Collectors.toMap(
                        row -> Long.valueOf(row.get("targetId").toString()),
                        row -> ((Number) row.get("cnt")).longValue()));
    }

    /** 校验评论对象类型 */
    private void checkTargetType(Integer targetType) {
        if (CommentTargetType.of(targetType) == null) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "无效的评论对象类型");
        }
    }

    /** 批量查询用户信息：整页评论人与被回复人去重后一次 Feign 请求聚合，失败时返回空表由调用方降级 */
    private Map<Long, UserVO> loadUserInfos(Collection<Long> userIds) {
        List<Long> ids = userIds.stream()
                .filter(Objects::nonNull)
                .distinct()
                .collect(Collectors.toList());
        if (ids.isEmpty()) {
            return Map.of();
        }
        try {
            Result<List<UserVO>> result = userFeignClient.listByIds(ids);
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode() && result.getData() != null) {
                return result.getData().stream()
                        .filter(user -> user.getId() != null)
                        .collect(Collectors.toMap(UserVO::getId, Function.identity(), (a, b) -> a));
            }
        } catch (Exception e) {
            log.warn("批量查询评论人信息失败，用户数={}", ids.size(), e);
        }
        return Map.of();
    }

    /** 评论 DO 转 VO，从聚合结果中附加评论人与被回复人昵称头像 */
    private CommentVO toVO(Comment comment, Map<Long, UserVO> users) {
        CommentVO vo = new CommentVO();
        BeanUtils.copyProperties(comment, vo);
        UserVO user = users.get(comment.getUserId());
        if (user != null) {
            vo.setUserNickname(user.getNickname());
            vo.setUserAvatar(user.getAvatar());
        }
        if (comment.getReplyUserId() != null) {
            UserVO replyUser = users.get(comment.getReplyUserId());
            vo.setReplyUserNickname(replyUser == null ? null : replyUser.getNickname());
        }
        return vo;
    }
}
