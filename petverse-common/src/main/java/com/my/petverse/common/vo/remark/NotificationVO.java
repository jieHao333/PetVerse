package com.my.petverse.common.vo.remark;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 站内通知返回实体（聚合触发人昵称头像，附类型/来源展示文案）
 */
@Data
public class NotificationVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 通知ID */
    private Long id;

    /** 通知类型：1-评论 2-回复 3-点赞 */
    private Integer type;

    /** 通知类型文案 */
    private String typeText;

    /** 通知来源：1-圈子动态 2-商品评价 */
    private Integer source;

    /** 通知来源文案 */
    private String sourceText;

    /** 触发人用户ID */
    private Long actorUserId;

    /** 触发人昵称（用户服务不可用时为空） */
    private String actorNickname;

    /** 触发人头像（用户服务不可用时为空） */
    private String actorAvatar;

    /** 内容摘要 */
    private String content;

    /** 跳转目标对象ID（动态ID或商品ID） */
    private Long targetId;

    /** 是否已读：0-未读 1-已读 */
    private Integer isRead;

    /** 通知时间 */
    private LocalDateTime createTime;
}
