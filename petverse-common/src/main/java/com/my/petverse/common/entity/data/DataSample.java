package com.my.petverse.common.entity.data;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 数据中台反馈样本层，对应表 data_sample。
 * 由曝光与点击行为按 用户+场景+目标+日期 关联生成，label 为是否点击，
 * 是后续召回/重排模型训练的标准样本，构成"消费→行为→样本→模型"的反馈闭环。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("data_sample")
public class DataSample extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    private Long userId;
    /** 场景：home/shop */
    private String scene;
    /** 目标对象类型：product/space */
    private String targetType;
    /** 目标对象ID */
    private Long targetId;
    /** 曝光位置 */
    private Integer position;
    /** 是否曝光：0/1 */
    private Integer exposed;
    /** 是否点击：0/1 */
    private Integer clicked;
    /** AI反馈：1/-1/0（非推荐目标为0） */
    private Integer feedback;
    /** 训练标签：是否点击 0/1 */
    private Integer label;
    /** 样本日期 */
    private LocalDate sampleDate;
    /** 事件时间 */
    private LocalDateTime eventTime;
}