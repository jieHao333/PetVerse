package com.my.petverse.common.dto.social;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 发起好友申请请求参数（发起人ID由网关注入的 X-User-Id 请求头提供）
 */
@Data
public class FriendRequestSendDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 目标用户ID */
    @NotNull(message = "目标用户ID不能为空")
    private Long toUserId;
}
