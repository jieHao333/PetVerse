package com.my.petverse.common.vo.social;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 好友信息返回实体（聚合用户基本资料）
 */
@Data
public class FriendVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 好友的用户ID */
    private Long userId;

    /** 登录用户名 */
    private String username;

    /** 昵称 */
    private String nickname;

    /** 头像地址 */
    private String avatar;

    /** 成为好友的时间 */
    private LocalDateTime createTime;
}
