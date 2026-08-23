package com.my.petverse.social.service;

import com.my.petverse.common.dto.social.FriendRequestSendDTO;
import com.my.petverse.common.vo.social.FriendRequestVO;
import com.my.petverse.common.vo.social.FriendVO;

import java.util.List;

/**
 * 好友服务接口
 */
public interface FriendService {

    /** 发起好友申请 */
    boolean sendRequest(Long userId, FriendRequestSendDTO dto);

    /** 查询我收到的好友申请（按时间倒序） */
    List<FriendRequestVO> listMyRequests(Long userId);

    /** 同意好友申请，建立双向好友关系 */
    boolean acceptRequest(Long userId, Long requestId);

    /** 拒绝好友申请 */
    boolean rejectRequest(Long userId, Long requestId);

    /** 查询我的好友列表 */
    List<FriendVO> listFriends(Long userId);

    /** 查询我的好友用户ID列表（供其他服务做可见性判断，不聚合用户资料） */
    List<Long> listFriendIds(Long userId);

    /** 删除好友（双向解除） */
    boolean removeFriend(Long userId, Long friendUserId);

    /** 判断两人是否为好友 */
    boolean isFriend(Long userId, Long friendUserId);
}
