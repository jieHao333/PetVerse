package com.my.petverse.remark.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.remark.LikeRecord;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.Collection;

/**
 * 点赞记录 Mapper，取消点赞走物理删除
 */
@Mapper
public interface LikeRecordMapper extends BaseMapper<LikeRecord> {

    /** 物理删除指定对象下某用户的点赞记录（点赞表无逻辑删除列） */
    @Delete("DELETE FROM like_record WHERE target_type = #{targetType} "
            + "AND target_id = #{targetId} AND user_id = #{userId}")
    int physicalDelete(@Param("targetType") Integer targetType,
                       @Param("targetId") Long targetId,
                       @Param("userId") Long userId);

    /** 批量新增点赞记录，一条 INSERT 写入一批（调用方按批次大小切分） */
    @Insert("<script>INSERT INTO like_record (id, target_type, target_id, user_id, create_time) VALUES "
            + "<foreach collection='records' item='record' separator=','>"
            + "(#{record.id}, #{record.targetType}, #{record.targetId}, #{record.userId}, NOW())"
            + "</foreach></script>")
    int insertBatch(@Param("records") Collection<LikeRecord> records);

    /** 批量物理删除指定对象下一批用户的点赞记录 */
    @Delete("<script>DELETE FROM like_record WHERE target_type = #{targetType} "
            + "AND target_id = #{targetId} AND user_id IN "
            + "<foreach collection='userIds' item='userId' open='(' separator=',' close=')'>#{userId}</foreach>"
            + "</script>")
    int physicalDeleteBatch(@Param("targetType") Integer targetType,
                            @Param("targetId") Long targetId,
                            @Param("userIds") Collection<Long> userIds);
}