package com.my.petverse.common.entity.social;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 聊天消息实体，对应数据库表 chat_message
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("chat_message")
public class ChatMessage extends BaseEntity {

    /** 发送者用户ID */
    private Long senderId;

    /** 接收者用户ID */
    private Long receiverId;

    /** 消息内容：文本消息存文本，图片/文件消息存 OSS 地址 */
    private String content;

    /** 消息类型 0-文本 1-图片 2-文件 */
    private Integer msgType;

    /** 文件原始名称（图片/文件消息时记录，供前端展示） */
    private String fileName;
}
