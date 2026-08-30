package com.my.petverse.common.dto.social;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 发起好友申请请求参数（发起人ID由服务端从登录令牌解析，经 UserContext 提供）
 */
@Data
public class FriendRequestSendDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 目标用户ID */
    @NotNull(message = "目标用户ID不能为空")
    private Long toUserId;
}
