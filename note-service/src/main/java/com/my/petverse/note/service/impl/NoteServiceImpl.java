package com.my.petverse.note.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.note.NotePageQueryDTO;
import com.my.petverse.common.dto.note.NoteSaveDTO;
import com.my.petverse.common.dto.note.NoteUpdateDTO;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.entity.note.Note;
import com.my.petverse.common.enums.NoteVisibility;
import com.my.petverse.common.enums.PetExpSource;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.note.NoteCreateVO;
import com.my.petverse.common.vo.note.NoteVO;
import com.my.petverse.common.vo.pet.PetExpGainVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.note.feign.PetFeignClient;
import com.my.petverse.note.feign.SocialFeignClient;
import com.my.petverse.note.feign.UserFeignClient;
import com.my.petverse.note.mapper.NoteMapper;
import com.my.petverse.note.service.NoteService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 笔记服务实现类
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NoteServiceImpl extends ServiceImpl<NoteMapper, Note> implements NoteService {

    private final PetFeignClient petFeignClient;

    private final SocialFeignClient socialFeignClient;

    private final UserFeignClient userFeignClient;

    /** 根据ID查询笔记 */
    @Override
    public NoteVO getNoteById(Long id) {
        Note note = getById(id);
        return note == null ? null : toVO(note);
    }

    /** 按可见性规则查询笔记详情，无权查看时视为不存在，避免泄露笔记存在性 */
    @Override
    public NoteVO getVisibleNote(Long id, Long callerId) {
        Note note = getById(id);
        if (note == null || !isVisibleTo(note, callerId)) {
            return null;
        }
        NoteVO vo = toVO(note);
        fillAuthor(List.of(vo));
        return vo;
    }

    /** 查询当前用户可见的笔记列表 */
    @Override
    public List<NoteVO> listNotes(Long callerId) {
        LambdaQueryWrapper<Note> wrapper = new LambdaQueryWrapper<>();
        applyVisibility(wrapper, callerId);
        List<NoteVO> vos = list(wrapper).stream().map(this::toVO).collect(Collectors.toList());
        fillAuthor(vos);
        return vos;
    }

    /** 分页查询当前用户可见的笔记 */
    @Override
    public PageResult<NoteVO> pageNotes(NotePageQueryDTO query, Long callerId) {
        LambdaQueryWrapper<Note> wrapper = new LambdaQueryWrapper<Note>()
                .like(StringUtils.hasText(query.getTitle()), Note::getTitle, query.getTitle())
                .eq(StringUtils.hasText(query.getCategory()), Note::getCategory, query.getCategory())
                .eq(query.getPetId() != null, Note::getPetId, query.getPetId())
                .eq(query.getUserId() != null, Note::getUserId, query.getUserId())
                .orderByDesc(Note::getCreateTime);
        applyVisibility(wrapper, callerId);
        Page<Note> page = page(new Page<>(query.getPageNum(), query.getPageSize()), wrapper);
        List<NoteVO> records = page.getRecords().stream().map(this::toVO).collect(Collectors.toList());
        fillAuthor(records);
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), records);
    }

    /**
     * 追加可见性过滤条件：公开笔记、本人笔记始终可见；
     * 仅好友笔记在作者是当前用户的好友时可见（好友关系双向对称）
     */
    private void applyVisibility(LambdaQueryWrapper<Note> wrapper, Long callerId) {
        List<Long> friendIds = listFriendIdsQuietly(callerId);
        wrapper.and(w -> {
            w.eq(Note::getVisibility, NoteVisibility.PUBLIC.getCode())
                    .or().eq(Note::getUserId, callerId);
            if (!friendIds.isEmpty()) {
                w.or(w1 -> w1.eq(Note::getVisibility, NoteVisibility.FRIENDS_ONLY.getCode())
                        .in(Note::getUserId, friendIds));
            }
        });
    }

    /** 判断笔记对指定用户是否可见 */
    private boolean isVisibleTo(Note note, Long callerId) {
        // 作者本人始终可见（含仅自己）
        if (note.getUserId() != null && note.getUserId().equals(callerId)) {
            return true;
        }
        NoteVisibility visibility = NoteVisibility.of(note.getVisibility());
        if (visibility == NoteVisibility.PUBLIC) {
            return true;
        }
        if (visibility == NoteVisibility.FRIENDS_ONLY) {
            return listFriendIdsQuietly(note.getUserId()).contains(callerId);
        }
        return false;
    }

    /** 调用社交服务获取好友ID，失败时降级为空列表（仅能看到公开笔记） */
    private List<Long> listFriendIdsQuietly(Long userId) {
        try {
            Result<List<Long>> result = socialFeignClient.listFriendIds(userId);
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode() && result.getData() != null) {
                return result.getData();
            }
        } catch (Exception e) {
            log.warn("调用社交服务获取好友列表失败，userId={}", userId, e);
        }
        return List.of();
    }

    /** 聚合作者用户名/昵称：同页作者去重后逐个查询，失败或已注销时降级展示，不阻断列表 */
    private void fillAuthor(List<NoteVO> vos) {
        if (vos.isEmpty()) {
            return;
        }
        Map<Long, UserVO> userCache = new HashMap<>();
        for (NoteVO vo : vos) {
            Long authorId = vo.getUserId();
            if (authorId == null) {
                continue;
            }
            if (!userCache.containsKey(authorId)) {
                userCache.put(authorId, getUserQuietly(authorId));
            }
            UserVO author = userCache.get(authorId);
            if (author != null) {
                vo.setAuthorUsername(author.getUsername());
                vo.setAuthorNickname(author.getNickname());
            } else {
                vo.setAuthorUsername("用户" + authorId);
            }
        }
    }

    /** 调用用户服务获取用户资料，失败或不存在时返回 null */
    private UserVO getUserQuietly(Long userId) {
        try {
            Result<UserVO> result = userFeignClient.getUserById(userId);
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode()) {
                return result.getData();
            }
        } catch (Exception e) {
            log.warn("调用用户服务获取作者信息失败，userId={}", userId, e);
        }
        return null;
    }

    /** 新增笔记，保存成功后为宠物发放经验奖励 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public NoteCreateVO saveNote(NoteSaveDTO dto) {
        Note note = new Note();
        BeanUtils.copyProperties(dto, note);
        // 可见性缺省为公开，非法值拒绝
        Integer visibility = checkVisibility(dto.getVisibility());
        note.setVisibility(visibility == null ? NoteVisibility.PUBLIC.getCode() : visibility);
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
        checkVisibility(dto.getVisibility());
        Integer originalVisibility = note.getVisibility();
        BeanUtils.copyProperties(dto, note);
        // 可见性未传时保持原值（BeanUtils 会用 null 覆盖）
        if (dto.getVisibility() == null) {
            note.setVisibility(originalVisibility);
        }
        return updateById(note);
    }

    /** 删除笔记 */
    @Override
    public boolean deleteNote(Long id) {
        return removeById(id);
    }

    /** 校验可见性取值，非法拒绝；为空返回 null 由调用方决定缺省策略 */
    private Integer checkVisibility(Integer visibility) {
        if (visibility == null) {
            return null;
        }
        if (visibility < NoteVisibility.PUBLIC.getCode() || visibility > NoteVisibility.PRIVATE.getCode()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "无效的笔记可见性");
        }
        return visibility;
    }

    /** DO 转 VO */
    private NoteVO toVO(Note note) {
        NoteVO vo = new NoteVO();
        BeanUtils.copyProperties(note, vo);
        return vo;
    }
}
