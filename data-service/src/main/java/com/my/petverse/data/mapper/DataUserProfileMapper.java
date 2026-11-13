package com.my.petverse.data.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.data.DataUserProfile;
import org.apache.ibatis.annotations.Mapper;

/**
 * DWS 用户画像 Mapper。
 */
@Mapper
public interface DataUserProfileMapper extends BaseMapper<DataUserProfile> {
}