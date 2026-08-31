package com.my.petverse.common.mq.message;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 动态发布事件消息：消费方为 pet-service，用于异步发放宠物经验奖励
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class SpaceCreatedMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 新建动态ID */
    private Long spaceId;

    /** 发布人用户ID */
    private Long userId;
}
