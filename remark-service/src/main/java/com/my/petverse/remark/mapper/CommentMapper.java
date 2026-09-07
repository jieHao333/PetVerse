package com.my.petverse.remark.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.remark.Comment;
import org.apache.ibatis.annotations.Mapper;

/**
 * 通用评论 Mapper
 */
@Mapper
public interface CommentMapper extends BaseMapper<Comment> {
}
