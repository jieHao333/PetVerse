package com.my.petverse.common.vo.data;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 模型出参。统一数据服务以此向 AI 侧下发激活模型，供推荐重排打分。
 */
@Data
public class ModelVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 模型键 */
    private String modelKey;
    /** 版本号 */
    private Integer version;
    /** 算法 */
    private String algorithm;
    /** 特征名列表 */
    private List<String> features;
    /** 权重（与特征一一对应，含偏置） */
    private List<Double> weights;
    /** 评估 AUC */
    private Double auc;
    /** 训练样本数 */
    private Integer sampleCount;
    /** 状态：0-停用 1-激活 */
    private Integer status;
    /** 备注 */
    private String remark;
    /** 训练时间 */
    private LocalDateTime trainedAt;
}