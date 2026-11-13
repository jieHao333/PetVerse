package com.my.petverse.data.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.data.DataMetricDaily;
import org.apache.ibatis.annotations.Mapper;

/**
 * ADS 按日指标 Mapper。
 */
@Mapper
public interface DataMetricDailyMapper extends BaseMapper<DataMetricDaily> {
}