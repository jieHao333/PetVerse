package com.my.petverse.social.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.social.FriendRequest;
import org.apache.ibatis.annotations.Mapper;

/**
 * 好友申请数据访问接口
 */
@Mapper
public interface FriendRequestMapper extends BaseMapper<FriendRequest> {
}
