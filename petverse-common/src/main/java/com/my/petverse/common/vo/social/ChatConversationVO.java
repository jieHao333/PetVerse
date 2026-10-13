package com.my.petverse.common.vo.social;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 聊天会话返回实体（消息列表项，聚合对方资料、好友标识与未读数）
 */
@Data
public class ChatConversationVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 对方用户ID */
    private Long friendUserId;

    /** 登录用户名 */
    private String username;

    /** 昵称 */
    private String nickname;

    /** 头像地址 */
    private String avatar;

    /** 对方是否为我的好友：非好友会话仅能发一条打招呼消息，前端据此提示 */
    private Boolean friend;

    /** 最后一条消息内容：文本存文本，图片/文件存 OSS 地址 */
    private String lastContent;

    /** 最后一条消息类型 0-文本 1-图片 2-文件 */
    private Integer lastMsgType;

    /** 最后一条消息发送时间 */
    private LocalDateTime lastTime;

    /** 未读消息数 */
    private Long unreadCount;
}
