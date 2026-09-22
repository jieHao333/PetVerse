package com.my.petverse.social.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.social.FriendRequestSendDTO;
import com.my.petverse.common.entity.social.FriendRequest;
import com.my.petverse.common.entity.social.Friendship;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.social.FriendRequestVO;
import com.my.petverse.common.vo.social.FriendVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.social.feign.UserFeignClient;
import com.my.petverse.social.mapper.FriendRequestMapper;
import com.my.petverse.social.mapper.FriendshipMapper;
import com.my.petverse.social.service.FriendService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 好友服务实现类
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class FriendServiceImpl extends ServiceImpl<FriendRequestMapper, FriendRequest> implements FriendService {

    /** 申请状态：待处理 */
    private static final int STATUS_PENDING = 0;

    /** 申请状态：已同意 */
    private static final int STATUS_ACCEPTED = 1;

    /** 申请状态：已拒绝 */
    private static final int STATUS_REJECTED = 2;

    private final FriendshipMapper friendshipMapper;

    private final UserFeignClient userFeignClient;

    /** 发起好友申请 */
    @Override
    public boolean sendRequest(Long userId, FriendRequestSendDTO dto) {
        Long toUserId = dto.getToUserId();
        if (toUserId.equals(userId)) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "不能添加自己为好友");
        }
        if (isFriend(userId, toUserId)) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "对方已经是你的好友了");
        }
        // 我已向对方发起过待处理的申请，不允许重复申请
        long myPending = count(new LambdaQueryWrapper<FriendRequest>()
                .eq(FriendRequest::getFromUserId, userId)
                .eq(FriendRequest::getToUserId, toUserId)
                .eq(FriendRequest::getStatus, STATUS_PENDING));
        if (myPending > 0) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "请勿重复申请，等待对方处理即可");
        }
        // 对方向我发起过待处理的申请，引导直接去同意
        long theirPending = count(new LambdaQueryWrapper<FriendRequest>()
                .eq(FriendRequest::getFromUserId, toUserId)
                .eq(FriendRequest::getToUserId, userId)
                .eq(FriendRequest::getStatus, STATUS_PENDING));
        if (theirPending > 0) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "对方向你发起了好友申请，请到好友申请中处理");
        }
        FriendRequest request = new FriendRequest();
        request.setFromUserId(userId);
        request.setToUserId(toUserId);
        request.setStatus(STATUS_PENDING);
        return save(request);
    }

    /** 查询我收到的好友申请，聚合申请人资料；资料获取失败时降级展示，不丢弃申请记录 */
    @Override
    public List<FriendRequestVO> listMyRequests(Long userId) {
        List<FriendRequest> requests = list(new LambdaQueryWrapper<FriendRequest>()
                .eq(FriendRequest::getToUserId, userId)
                .orderByDesc(FriendRequest::getCreateTime)
                .last("limit 50"));
        List<FriendRequestVO> result = new ArrayList<>();
        // 申请人资料一次批量查询，申请记录逐条聚合
        Map<Long, UserVO> users = loadUsersQuietly(requests.stream()
                .map(FriendRequest::getFromUserId)
                .collect(Collectors.toSet()));
        for (FriendRequest request : requests) {
            UserVO fromUser = users.get(request.getFromUserId());
            FriendRequestVO vo = new FriendRequestVO();
            vo.setId(request.getId());
            if (fromUser != null) {
                vo.setFromUserId(fromUser.getId());
                vo.setFromUsername(fromUser.getUsername());
                vo.setFromNickname(fromUser.getNickname());
                vo.setFromAvatar(fromUser.getAvatar());
            } else {
                // 用户服务不可用或用户已注销时，用库内ID降级展示，保证申请记录不丢失
                vo.setFromUserId(request.getFromUserId());
                vo.setFromUsername("用户" + request.getFromUserId());
                vo.setFromNickname("未知用户");
            }
            vo.setStatus(request.getStatus());
            vo.setCreateTime(request.getCreateTime());
            result.add(vo);
        }
        return result;
    }

    /** 同意好友申请，建立双向好友关系 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean acceptRequest(Long userId, Long requestId) {
        FriendRequest request = getAndCheckRequest(requestId, userId);
        request.setStatus(STATUS_ACCEPTED);
        boolean updated = updateById(request);
        // 双向写入好友关系，已存在则跳过
        addFriendshipIfAbsent(request.getFromUserId(), request.getToUserId());
        addFriendshipIfAbsent(request.getToUserId(), request.getFromUserId());
        return updated;
    }

    /** 拒绝好友申请 */
    @Override
    public boolean rejectRequest(Long userId, Long requestId) {
        FriendRequest request = getAndCheckRequest(requestId, userId);
        request.setStatus(STATUS_REJECTED);
        return updateById(request);
    }

    /** 查询我的好友列表，聚合好友的用户资料 */
    @Override
    public List<FriendVO> listFriends(Long userId) {
        List<Friendship> friendships = friendshipMapper.selectList(new LambdaQueryWrapper<Friendship>()
                .eq(Friendship::getUserId, userId)
                .orderByDesc(Friendship::getCreateTime));
        List<FriendVO> result = new ArrayList<>();
        // 好友资料一次批量查询，关系记录逐条聚合
        Map<Long, UserVO> users = loadUsersQuietly(friendships.stream()
                .map(Friendship::getFriendUserId)
                .collect(Collectors.toSet()));
        for (Friendship friendship : friendships) {
            UserVO friendUser = users.get(friendship.getFriendUserId());
            FriendVO vo = new FriendVO();
            if (friendUser != null) {
                vo.setUserId(friendUser.getId());
                vo.setUsername(friendUser.getUsername());
                vo.setNickname(friendUser.getNickname());
                vo.setAvatar(friendUser.getAvatar());
            } else {
                // 用户服务不可用或用户已注销时，用库内ID降级展示，保证好友关系不丢失
                vo.setUserId(friendship.getFriendUserId());
                vo.setUsername("用户" + friendship.getFriendUserId());
                vo.setNickname("未知用户");
            }
            vo.setCreateTime(friendship.getCreateTime());
            result.add(vo);
        }
        return result;
    }

    /** 查询我的好友用户ID列表，仅查关系表不聚合资料，供其他服务做可见性判断 */
    @Override
    public List<Long> listFriendIds(Long userId) {
        List<Friendship> friendships = friendshipMapper.selectList(new LambdaQueryWrapper<Friendship>()
                .eq(Friendship::getUserId, userId)
                .select(Friendship::getFriendUserId));
        List<Long> ids = new ArrayList<>();
        for (Friendship friendship : friendships) {
            ids.add(friendship.getFriendUserId());
        }
        return ids;
    }

    /** 删除好友，双向解除关系 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean removeFriend(Long userId, Long friendUserId) {
        friendshipMapper.delete(new LambdaQueryWrapper<Friendship>()
                .eq(Friendship::getUserId, userId)
                .eq(Friendship::getFriendUserId, friendUserId));
        friendshipMapper.delete(new LambdaQueryWrapper<Friendship>()
                .eq(Friendship::getUserId, friendUserId)
                .eq(Friendship::getFriendUserId, userId));
        return true;
    }

    /** 判断两人是否为好友 */
    @Override
    public boolean isFriend(Long userId, Long friendUserId) {
        return friendshipMapper.selectCount(new LambdaQueryWrapper<Friendship>()
                .eq(Friendship::getUserId, userId)
                .eq(Friendship::getFriendUserId, friendUserId)) > 0;
    }

    /** 校验申请存在、待处理且当前用户是接收方 */
    private FriendRequest getAndCheckRequest(Long requestId, Long userId) {
        FriendRequest request = getById(requestId);
        if (request == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "好友申请不存在");
        }
        if (!request.getToUserId().equals(userId)) {
            throw new BusinessException(ResultCode.FORBIDDEN, "只能处理发给自己的好友申请");
        }
        if (request.getStatus() == null || request.getStatus() != STATUS_PENDING) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "该申请已处理，请勿重复操作");
        }
        return request;
    }

    /** 写入单向好友关系，已存在则跳过 */
    private void addFriendshipIfAbsent(Long userId, Long friendUserId) {
        long exist = friendshipMapper.selectCount(new LambdaQueryWrapper<Friendship>()
                .eq(Friendship::getUserId, userId)
                .eq(Friendship::getFriendUserId, friendUserId));
        if (exist > 0) {
            return;
        }
        Friendship friendship = new Friendship();
        friendship.setUserId(userId);
        friendship.setFriendUserId(friendUserId);
        friendshipMapper.insert(friendship);
    }

    /** 批量查询用户资料：一次请求聚合整页用户，失败或用户已注销时返回空表，由调用方降级展示 */
    private Map<Long, UserVO> loadUsersQuietly(Collection<Long> userIds) {
        List<Long> ids = userIds.stream()
                .filter(Objects::nonNull)
                .distinct()
                .collect(Collectors.toList());
        if (ids.isEmpty()) {
            return Map.of();
        }
        try {
            Result<List<UserVO>> result = userFeignClient.listByIds(ids);
            if (result == null) {
                log.warn("用户服务返回空结果，用户数={}", ids.size());
                return Map.of();
            }
            if (result.getCode() != ResultCode.SUCCESS.getCode()) {
                log.warn("用户服务返回异常状态码，用户数={}, code={}, msg={}", ids.size(), result.getCode(), result.getMsg());
                return Map.of();
            }
            if (result.getData() == null) {
                return Map.of();
            }
            return result.getData().stream()
                    .filter(user -> user.getId() != null)
                    .collect(Collectors.toMap(UserVO::getId, Function.identity(), (a, b) -> a));
        } catch (Exception e) {
            log.warn("批量查询用户资料失败，用户数={}", ids.size(), e);
            return Map.of();
        }
    }
}
