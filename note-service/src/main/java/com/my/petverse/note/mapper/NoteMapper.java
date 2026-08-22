package com.my.petverse.note.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.note.Note;
import org.apache.ibatis.annotations.Mapper;

/**
 * 笔记数据访问接口
 */
@Mapper
public interface NoteMapper extends BaseMapper<Note> {
}
