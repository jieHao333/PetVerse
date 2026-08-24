package com.my.petverse.space.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.space.Space;
import org.apache.ibatis.annotations.Mapper;

/**
 * 宠域空间动态数据访问接口
 */
@Mapper
public interface SpaceMapper extends BaseMapper<Space> {
}
