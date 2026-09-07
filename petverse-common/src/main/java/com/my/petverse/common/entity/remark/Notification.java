package com.my.petverse.common.entity.remark;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 站内通知实体，对应数据库表 notification
 * 记录"谁(actorUserId) 因某来源(source) 对某对象(targetId) 做了什么(type) → 通知谁(userId)"
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("notification")
public class Notification extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 收件人用户ID（被通知人） */
    private Long userId;

    /** 触发人用户ID */
    private Long actorUserId;

    /** 通知类型，见 NotificationType：1-评论 2-回复 3-点赞 */
    private Integer type;

    /** 通知来源，见 NotificationSource：1-圈子动态 2-商品评价 */
    private Integer source;

    /** 跳转目标对象ID（动态ID或商品ID） */
    private Long targetId;

    /** 内容摘要 */
    private String content;

    /** 是否已读：0-未读 1-已读 */
    private Integer isRead;
}
