package com.my.petverse.common.vo.space;

import lombok.Data;

import java.io.Serializable;

/**
 * 发布动态结果返回实体。
 * 宠物经验奖励已改为 RocketMQ 事件驱动异步发放，不再同步返回，前端不再展示同步经验结果
 */
@Data
public class SpaceCreateVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 新建动态ID */
    private Long spaceId;
}
