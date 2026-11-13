package com.my.petverse.data.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.data.DataSample;
import org.apache.ibatis.annotations.Mapper;

/**
 * 反馈样本 Mapper。
 */
@Mapper
public interface DataSampleMapper extends BaseMapper<DataSample> {
}