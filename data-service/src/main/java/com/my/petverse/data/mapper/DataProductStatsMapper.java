package com.my.petverse.data.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.data.DataProductStats;
import org.apache.ibatis.annotations.Mapper;

/**
 * DWS 商品统计 Mapper。
 */
@Mapper
public interface DataProductStatsMapper extends BaseMapper<DataProductStats> {
}