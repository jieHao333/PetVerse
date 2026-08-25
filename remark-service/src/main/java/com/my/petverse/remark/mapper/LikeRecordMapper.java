package com.my.petverse.remark.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.remark.LikeRecord;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

/**
 * 点赞记录 Mapper，取消点赞走物理删除
 */
@Mapper
public interface LikeRecordMapper extends BaseMapper<LikeRecord> {

    /** 物理删除指定对象下某用户的点赞记录（点赞表无逻辑删除列） */
    @Delete("DELETE FROM like_record WHERE target_type = #{targetType} " +
            "AND target_id = #{targetId} AND user_id = #{userId}")
    int physicalDelete(@Param("targetType") Integer targetType,
                       @Param("targetId") Long targetId,
                       @Param("userId") Long userId);
}
