package com.my.petverse.common.dto.social;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 发送聊天消息请求参数（发送者ID由网关注入的 X-User-Id 请求头提供）
 */
@Data
public class ChatMessageSendDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 接收者用户ID */
    @NotNull(message = "接收者ID不能为空")
    private Long receiverId;

    /** 消息内容 */
    @NotBlank(message = "消息内容不能为空")
    @Size(max = 500, message = "消息内容不能超过500个字符")
    private String content;
}
