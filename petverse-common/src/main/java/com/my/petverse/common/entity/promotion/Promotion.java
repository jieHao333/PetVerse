package com.my.petverse.common.entity.promotion;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * 促销活动实体，对应数据库表 promotion（形式多种多样，例如优惠券；当前预留扩展）
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("promotion")
public class Promotion extends BaseEntity {

    /** 活动名称 */
    private String name;

    /** 促销活动类型：1-优惠券 2-分销 */
    private Integer type;

    /** 是否是热门活动：1-是 0-否 */
    private Integer hot;

    /** 活动开始时间 */
    private LocalDateTime beginTime;

    /** 活动结束时间 */
    private LocalDateTime endTime;
}
