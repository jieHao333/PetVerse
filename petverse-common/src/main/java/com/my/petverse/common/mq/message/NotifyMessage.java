package com.my.petverse.common.mq.message;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 站内通知事件：由具备收件人信息的业务侧发出（评论回复、评价回复、点赞动态），
 * 消费方为 remark-service，统一落库为一条通知记录
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class NotifyMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 收件人用户ID（被通知人） */
    private Long recipientUserId;

    /** 触发人用户ID（谁做出的互动，用于展示"来自谁"） */
    private Long actorUserId;

    /** 通知类型，取值见 NotificationType：1-评论 2-回复 3-点赞 */
    private Integer type;

    /** 通知来源，取值见 NotificationSource：1-圈子动态 2-商品评价 */
    private Integer source;

    /** 跳转目标对象ID（评论回复/点赞为动态ID，评价回复为商品ID） */
    private Long targetId;

    /** 内容摘要（回复/评论正文；点赞等无正文时可为空） */
    private String content;
}
