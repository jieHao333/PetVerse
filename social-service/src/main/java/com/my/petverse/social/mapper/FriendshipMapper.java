package com.my.petverse.social.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.social.Friendship;
import org.apache.ibatis.annotations.Mapper;

/**
 * 好友关系数据访问接口
 */
@Mapper
public interface FriendshipMapper extends BaseMapper<Friendship> {
}
