package com.my.petverse.common.entity.data;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 数据中台模型注册表，对应表 data_model。
 * 记录每次训练产出的模型版本（算法/特征/权重/评估指标/状态），构成"样本→训练→注册→服务"闭环的注册环节。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("data_model")
public class DataModel extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 模型键（如 rerank_ctr 推荐重排点击率模型） */
    private String modelKey;
    /** 版本号（同一模型键内递增） */
    private Integer version;
    /** 算法（如 logreg 逻辑回归） */
    private String algorithm;
    /** 特征名列表（JSON 数组，定义训练/服务一致的特征口径） */
    private String features;
    /** 权重（JSON 数组，与特征一一对应，含偏置项） */
    private String weights;
    /** 评估 AUC（留出集） */
    private Double auc;
    /** 训练样本数 */
    private Integer sampleCount;
    /** 状态：0-停用 1-激活（同一模型键仅一个激活） */
    private Integer status;
    /** 备注 */
    private String remark;
}