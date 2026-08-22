package com.my.petverse.common.entity.social;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 好友申请实体，对应数据库表 friend_request
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("friend_request")
public class FriendRequest extends BaseEntity {

    /** 发起申请的用户ID */
    private Long fromUserId;

    /** 接收申请的用户ID */
    private Long toUserId;

    /** 申请状态：0-待处理 1-已同意 2-已拒绝 */
    private Integer status;
}
