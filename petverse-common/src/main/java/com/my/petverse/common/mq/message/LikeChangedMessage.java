package com.my.petverse.common.mq.message;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 点赞变更事件：携带变更后的最新点赞数，
 * 消费方按对象类型更新热度冗余列与检索索引
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class LikeChangedMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 点赞对象类型，取值见 LikeTargetType */
    private Integer targetType;

    /** 点赞对象ID */
    private Long targetId;

    /** 变更后的最新点赞数 */
    private Long count;

    /** 触发本次变更的用户ID（点赞人），供点赞通知解析 */
    private Long actorUserId;

    /** true-新增点赞 false-取消点赞，仅新增点赞需要生成通知 */
    private Boolean added;

    public LikeChangedMessage(Integer targetType, Long targetId, Long count) {
        this.targetType = targetType;
        this.targetId = targetId;
        this.count = count;
    }
}
