package com.my.petverse.note.controller;

import com.my.petverse.common.dto.note.NotePageQueryDTO;
import com.my.petverse.common.dto.note.NoteSaveDTO;
import com.my.petverse.common.dto.note.NoteUpdateDTO;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.note.NoteCreateVO;
import com.my.petverse.common.vo.note.NoteVO;
import com.my.petverse.note.service.NoteService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 笔记接口控制器
 */
@RestController
@RequestMapping("/note")
@RequiredArgsConstructor
public class NoteController {

    private final NoteService noteService;

    /** 根据ID查询笔记，按当前用户可见性过滤，无权查看时视为不存在 */
    @GetMapping("/{id}")
    public Result<NoteVO> getById(@PathVariable("id") Long id,
                                  @RequestHeader("X-User-Id") Long userId) {
        return Result.success(noteService.getVisibleNote(id, userId));
    }

    /** 查询当前用户可见的笔记列表 */
    @GetMapping("/list")
    public Result<List<NoteVO>> list(@RequestHeader("X-User-Id") Long userId) {
        return Result.success(noteService.listNotes(userId));
    }

    /** 分页查询当前用户可见的笔记 */
    @GetMapping("/page")
    public Result<PageResult<NoteVO>> page(NotePageQueryDTO query,
                                           @RequestHeader("X-User-Id") Long userId) {
        return Result.success(noteService.pageNotes(query, userId));
    }

    /** 新增笔记，同时为宠物发放经验奖励 */
    @PostMapping
    public Result<NoteCreateVO> save(@RequestBody @Valid NoteSaveDTO dto) {
        return Result.success(noteService.saveNote(dto));
    }

    /** 修改笔记，仅作者本人可操作 */
    @PutMapping
    public Result<Boolean> update(@RequestHeader("X-User-Id") Long userId,
                                  @RequestBody @Valid NoteUpdateDTO dto) {
        checkOwnership(dto.getId(), userId);
        return Result.success(noteService.updateNote(dto));
    }

    /** 删除笔记，仅作者本人可操作 */
    @DeleteMapping("/{id}")
    public Result<Boolean> delete(@PathVariable("id") Long id,
                                  @RequestHeader("X-User-Id") Long userId) {
        checkOwnership(id, userId);
        return Result.success(noteService.deleteNote(id));
    }

    /** 校验笔记归属：笔记不存在或不属于当前用户时拒绝操作 */
    private void checkOwnership(Long noteId, Long userId) {
        NoteVO note = noteService.getNoteById(noteId);
        if (note == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "笔记不存在");
        }
        if (note.getUserId() == null || !note.getUserId().equals(userId)) {
            throw new BusinessException(ResultCode.FORBIDDEN, "只能操作自己发布的笔记");
        }
    }
}
