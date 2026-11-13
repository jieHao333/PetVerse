package com.my.petverse.data.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.data.DataEventRaw;
import org.apache.ibatis.annotations.Mapper;

/**
 * ODS 原始事件 Mapper。
 */
@Mapper
public interface DataEventRawMapper extends BaseMapper<DataEventRaw> {
}