package com.my.petverse.note.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.note.NotePageQueryDTO;
import com.my.petverse.common.dto.note.NoteSaveDTO;
import com.my.petverse.common.dto.note.NoteUpdateDTO;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.entity.note.Note;
import com.my.petverse.common.enums.PetExpSource;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.note.NoteCreateVO;
import com.my.petverse.common.vo.note.NoteVO;
import com.my.petverse.common.vo.pet.PetExpGainVO;
import com.my.petverse.note.feign.PetFeignClient;
import com.my.petverse.note.mapper.NoteMapper;
import com.my.petverse.note.service.NoteService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.util.List;
import java.util.stream.Collectors;

/**
 * 笔记服务实现类
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NoteServiceImpl extends ServiceImpl<NoteMapper, Note> implements NoteService {

    private final PetFeignClient petFeignClient;

    /** 根据ID查询笔记 */
    @Override
    public NoteVO getNoteById(Long id) {
        Note note = getById(id);
        return note == null ? null : toVO(note);
    }

    /** 查询笔记列表 */
    @Override
    public List<NoteVO> listNotes() {
        return list().stream().map(this::toVO).collect(Collectors.toList());
    }

    /** 分页查询笔记 */
    @Override
    public PageResult<NoteVO> pageNotes(NotePageQueryDTO query) {
        LambdaQueryWrapper<Note> wrapper = new LambdaQueryWrapper<Note>()
                .like(StringUtils.hasText(query.getTitle()), Note::getTitle, query.getTitle())
                .eq(StringUtils.hasText(query.getCategory()), Note::getCategory, query.getCategory())
                .eq(query.getPetId() != null, Note::getPetId, query.getPetId())
                .eq(query.getUserId() != null, Note::getUserId, query.getUserId())
                .orderByDesc(Note::getCreateTime);
        Page<Note> page = page(new Page<>(query.getPageNum(), query.getPageSize()), wrapper);
        List<NoteVO> records = page.getRecords().stream().map(this::toVO).collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), records);
    }

    /** 新增笔记，保存成功后为宠物发放经验奖励 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public NoteCreateVO saveNote(NoteSaveDTO dto) {
        Note note = new Note();
        BeanUtils.copyProperties(dto, note);
        save(note);

        // 发布笔记奖励宠物经验，经验发放失败仅记录日志，不影响笔记保存
        PetExpGainVO petExp = null;
        try {
            PetExpGrantDTO grantDTO = new PetExpGrantDTO();
            grantDTO.setUserId(dto.getUserId());
            grantDTO.setSource(PetExpSource.NOTE.name());
            Result<PetExpGainVO> result = petFeignClient.grantExp(grantDTO);
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode()) {
                petExp = result.getData();
            }
        } catch (Exception e) {
            log.warn("发布笔记后为宠物发放经验失败，userId={}", dto.getUserId(), e);
        }

        NoteCreateVO vo = new NoteCreateVO();
        vo.setNoteId(note.getId());
        vo.setPetExp(petExp);
        return vo;
    }

    /** 修改笔记 */
    @Override
    public boolean updateNote(NoteUpdateDTO dto) {
        Note note = getById(dto.getId());
        if (note == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "笔记不存在");
        }
        BeanUtils.copyProperties(dto, note);
        return updateById(note);
    }

    /** 删除笔记 */
    @Override
    public boolean deleteNote(Long id) {
        return removeById(id);
    }

    /** DO 转 VO */
    private NoteVO toVO(Note note) {
        NoteVO vo = new NoteVO();
        BeanUtils.copyProperties(note, vo);
        return vo;
    }
}
