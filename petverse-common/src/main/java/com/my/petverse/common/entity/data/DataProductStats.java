package com.my.petverse.common.entity.data;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 数据中台 DWS 商品统计层，对应表 data_product_stats。
 * 由行为明细聚合曝光/点击/CTR 等，供推荐重排与商家经营分析复用。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("data_product_stats")
public class DataProductStats extends BaseEntity {

    private static final long serialVersionUID = 1L;

    /** 商品ID（唯一） */
    private Long productId;
    /** 累计曝光次数 */
    private Long exposureCnt;
    /** 累计点击次数 */
    private Long clickCnt;
    /** 点击率 click/exposure */
    private Double ctr;
    /** 累计下单次数（来自下单埋点/业务事件） */
    private Long orderCnt;
    /** 累计评价次数 */
    private Long reviewCnt;
    /** 热度分（点击/曝光/CTR 加权，供召回与排序） */
    private Double hotScore;
}