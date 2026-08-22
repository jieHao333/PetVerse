package com.my.petverse.note.controller;

import com.my.petverse.common.dto.note.NotePageQueryDTO;
import com.my.petverse.common.dto.note.NoteSaveDTO;
import com.my.petverse.common.dto.note.NoteUpdateDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
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

    /** 根据ID查询笔记 */
    @GetMapping("/{id}")
    public Result<NoteVO> getById(@PathVariable("id") Long id) {
        return Result.success(noteService.getNoteById(id));
    }

    /** 查询笔记列表 */
    @GetMapping("/list")
    public Result<List<NoteVO>> list() {
        return Result.success(noteService.listNotes());
    }

    /** 分页查询笔记 */
    @GetMapping("/page")
    public Result<PageResult<NoteVO>> page(NotePageQueryDTO query) {
        return Result.success(noteService.pageNotes(query));
    }

    /** 新增笔记，同时为宠物发放经验奖励 */
    @PostMapping
    public Result<NoteCreateVO> save(@RequestBody @Valid NoteSaveDTO dto) {
        return Result.success(noteService.saveNote(dto));
    }

    /** 修改笔记 */
    @PutMapping
    public Result<Boolean> update(@RequestBody @Valid NoteUpdateDTO dto) {
        return Result.success(noteService.updateNote(dto));
    }

    /** 删除笔记 */
    @DeleteMapping("/{id}")
    public Result<Boolean> delete(@PathVariable("id") Long id) {
        return Result.success(noteService.deleteNote(id));
    }
}
