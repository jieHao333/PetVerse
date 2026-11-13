package com.my.petverse.common.vo.data;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

/**
 * 用户画像/特征出参。统一数据服务（Data API）以此向 AI 侧一次返回治理好的特征。
 */
@Data
public class UserProfileVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    private Long userId;
    /** 饲养物种（逗号分隔） */
    private String ownSpecies;
    /** 物种亲和度 {物种: 权重} */
    private Map<String, Object> speciesAffinity;
    /** 活跃度分级 */
    private String activityLevel;
    /** 消费力分级 */
    private String consumptionLevel;
    /** 累计曝光次数 */
    private Long exposureCnt;
    /** 累计点击次数 */
    private Long clickCnt;
    /** 点击率 */
    private Double ctr;
    /** 累计搜索次数 */
    private Long searchCnt;
    /** AI有用反馈次数 */
    private Long aiFeedbackUp;
    /** AI无用反馈次数 */
    private Long aiFeedbackDown;
    /** 标签列表 */
    private List<String> tags;
    /** 最近活跃时间 */
    private LocalDateTime lastActiveTime;
    /** 特征版本号 */
    private Integer featureVersion;
}