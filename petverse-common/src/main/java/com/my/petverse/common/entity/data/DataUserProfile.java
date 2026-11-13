package com.my.petverse.common.entity.data;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * 数据中台 DWS 用户画像/特征层，对应表 data_user_profile。
 * 由行为明细与业务事件微批聚合而成，供统一数据服务（Data API）一次取数，替代 AI 侧逐接口现拉。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("data_user_profile")
public class DataUserProfile extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 用户ID（唯一） */
    private Long userId;
    /** 饲养物种（逗号分隔，来自宠物事件与行为推断） */
    private String ownSpecies;
    /** 物种亲和度（JSON：{物种: 权重}，由点击/搜索加权聚合） */
    private String speciesAffinity;
    /** 活跃度分级：low/medium/high */
    private String activityLevel;
    /** 消费力分级：low/medium/high */
    private String consumptionLevel;
    /** 累计曝光次数 */
    private Long exposureCnt;
    /** 累计点击次数 */
    private Long clickCnt;
    /** 点击率 click/exposure */
    private Double ctr;
    /** 累计搜索次数 */
    private Long searchCnt;
    /** AI回答有用反馈次数 */
    private Long aiFeedbackUp;
    /** AI回答无用反馈次数 */
    private Long aiFeedbackDown;
    /** 最近活跃时间 */
    private LocalDateTime lastActiveTime;
    /** 标签（JSON 数组） */
    private String tags;
    /** 特征版本号（口径调整时递增，便于缓存失效与灰度） */
    private Integer featureVersion;
}