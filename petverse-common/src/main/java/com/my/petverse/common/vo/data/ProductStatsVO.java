package com.my.petverse.common.vo.data;

import lombok.Data;

import java.io.Serializable;

/**
 * 商品统计出参，供推荐重排与商家经营分析。
 */
@Data
public class ProductStatsVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品ID */
    private Long productId;
    /** 累计曝光次数 */
    private Long exposureCnt;
    /** 累计点击次数 */
    private Long clickCnt;
    /** 点击率 */
    private Double ctr;
    /** 累计下单次数 */
    private Long orderCnt;
    /** 累计评价次数 */
    private Long reviewCnt;
    /** 热度分 */
    private Double hotScore;
}