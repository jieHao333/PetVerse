package com.my.petverse.common.vo.social;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 好友申请返回实体（聚合申请人基本资料）
 */
@Data
public class FriendRequestVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 申请记录ID */
    private Long id;

    /** 申请人用户ID */
    private Long fromUserId;

    /** 申请人登录用户名 */
    private String fromUsername;

    /** 申请人昵称 */
    private String fromNickname;

    /** 申请人头像 */
    private String fromAvatar;

    /** 申请状态：0-待处理 1-已同意 2-已拒绝 */
    private Integer status;

    /** 申请时间 */
    private LocalDateTime createTime;
}
