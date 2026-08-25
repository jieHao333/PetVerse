package com.my.petverse.common.vo.social;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 聊天消息返回实体
 */
@Data
public class ChatMessageVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 消息ID */
    private Long id;

    /** 发送者用户ID */
    private Long senderId;

    /** 接收者用户ID */
    private Long receiverId;

    /** 消息内容：文本消息存文本，图片/文件消息存 OSS 地址 */
    private String content;

    /** 消息类型 0-文本 1-图片 2-文件 */
    private Integer msgType;

    /** 文件原始名称（图片/文件消息时记录） */
    private String fileName;

    /** 发送时间 */
    private LocalDateTime createTime;
}
