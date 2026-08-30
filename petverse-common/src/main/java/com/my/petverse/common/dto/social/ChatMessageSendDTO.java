package com.my.petverse.common.dto.social;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 发送聊天消息请求参数（发送者ID由服务端从登录令牌解析，经 UserContext 提供）
 */
@Data
public class ChatMessageSendDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 接收者用户ID */
    @NotNull(message = "接收者ID不能为空")
    private Long receiverId;

    /** 消息内容：文本消息存文本，图片/文件消息存上传后的 OSS 地址 */
    @NotBlank(message = "消息内容不能为空")
    @Size(max = 500, message = "消息内容不能超过500个字符")
    private String content;

    /** 消息类型 0-文本 1-图片 2-文件，不传默认文本 */
    private Integer msgType;

    /** 文件原始名称（图片/文件消息时传入） */
    @Size(max = 255, message = "文件名称过长")
    private String fileName;
}
