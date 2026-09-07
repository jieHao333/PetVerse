package com.my.petverse.common.entity.social;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * 聊天会话实体，对应数据库表 chat_conversation
 * 每对好友按"查看者"维度存一条记录，用于记录已读时间与清空(删除)会话时间
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("chat_conversation")
public class ChatConversation extends BaseEntity {

    /** 会话所属用户ID（查看者） */
    private Long userId;

    /** 对方（好友）用户ID */
    private Long friendUserId;

    /** 最后已读时间：对方在该时间之后发来的消息计为未读 */
    private LocalDateTime lastReadTime;

    /** 清空(删除)会话时间：该时间之前的消息不在消息列表展示 */
    private LocalDateTime clearTime;
}
