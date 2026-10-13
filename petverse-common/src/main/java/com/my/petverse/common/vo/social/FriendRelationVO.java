package com.my.petverse.common.vo.social;

import lombok.Data;

import java.io.Serializable;

/**
 * 我与某个用户之间的好友关系状态，供用户主页、聊天页判断展示「加好友 / 已申请 / 已是好友」
 */
@Data
public class FriendRelationVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 目标用户ID */
    private Long userId;

    /** 是否已是好友 */
    private Boolean friend;

    /** 我是否已向对方发起待处理的好友申请（申请中，不可重复发起） */
    private Boolean requestSent;

    /** 对方向我发起的待处理好友申请ID，非空时可直接同意 */
    private Long receivedRequestId;
}
