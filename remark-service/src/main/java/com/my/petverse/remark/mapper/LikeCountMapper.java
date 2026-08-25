package com.my.petverse.remark.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.remark.LikeCount;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

/**
 * 点赞计数快照 Mapper
 */
@Mapper
public interface LikeCountMapper extends BaseMapper<LikeCount> {

    /** 按联合主键 (target_type, target_id) 插入或覆盖计数快照 */
    @Insert("INSERT INTO like_count (target_type, target_id, count, update_time) " +
            "VALUES (#{targetType}, #{targetId}, #{count}, NOW()) " +
            "ON DUPLICATE KEY UPDATE count = #{count}, update_time = NOW()")
    int upsert(@Param("targetType") Integer targetType,
               @Param("targetId") Long targetId,
               @Param("count") Integer count);
}
