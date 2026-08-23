package com.my.petverse.note.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.note.NotePageQueryDTO;
import com.my.petverse.common.dto.note.NoteSaveDTO;
import com.my.petverse.common.dto.note.NoteUpdateDTO;
import com.my.petverse.common.entity.note.Note;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.note.NoteCreateVO;
import com.my.petverse.common.vo.note.NoteVO;

import java.util.List;

/**
 * 笔记服务接口
 */
public interface NoteService extends IService<Note> {

    /** 根据ID查询笔记 */
    NoteVO getNoteById(Long id);

    /** 按可见性规则查询笔记详情，无权查看或不存在时返回 null */
    NoteVO getVisibleNote(Long id, Long callerId);

    /** 查询当前用户可见的笔记列表 */
    List<NoteVO> listNotes(Long callerId);

    /** 分页查询当前用户可见的笔记 */
    PageResult<NoteVO> pageNotes(NotePageQueryDTO query, Long callerId);

    /** 新增笔记并为宠物发放经验奖励，返回创建结果 */
    NoteCreateVO saveNote(NoteSaveDTO dto);

    /** 修改笔记 */
    boolean updateNote(NoteUpdateDTO dto);

    /** 删除笔记 */
    boolean deleteNote(Long id);
}
