package com.my.petverse.space.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.dto.space.SpaceMediaItemDTO;
import com.my.petverse.common.dto.space.SpacePageQueryDTO;
import com.my.petverse.common.dto.space.SpaceSaveDTO;
import com.my.petverse.common.dto.space.SpaceUpdateDTO;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.entity.space.SpaceMedia;
import com.my.petverse.common.enums.LikeTargetType;
import com.my.petverse.common.enums.PetExpSource;
import com.my.petverse.common.enums.SpaceVisibility;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.oss.OssService;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.pet.PetExpGainVO;
import com.my.petverse.common.vo.remark.LikeBatchVO;
import com.my.petverse.common.vo.space.SpaceCreateVO;
import com.my.petverse.common.vo.space.SpaceMediaItemVO;
import com.my.petverse.common.vo.space.SpaceMediaUploadVO;
import com.my.petverse.common.vo.space.SpaceVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.space.feign.PetFeignClient;
import com.my.petverse.space.feign.RemarkFeignClient;
import com.my.petverse.space.feign.SocialFeignClient;
import com.my.petverse.space.feign.UserFeignClient;
import com.my.petverse.space.mapper.SpaceMapper;
import com.my.petverse.space.mapper.SpaceMediaMapper;
import com.my.petverse.space.service.SpaceService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 宠域空间动态服务实现类
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class SpaceServiceImpl extends ServiceImpl<SpaceMapper, Space> implements SpaceService {

    private final PetFeignClient petFeignClient;

    private final SocialFeignClient socialFeignClient;

    private final UserFeignClient userFeignClient;

    private final RemarkFeignClient remarkFeignClient;

    private final SpaceMediaMapper spaceMediaMapper;

    private final OssService ossService;

    /** 媒体类型：图片 */
    private static final int MEDIA_IMAGE = 0;

    /** 媒体类型：视频 */
    private static final int MEDIA_VIDEO = 1;

    /** 单条动态媒体数量上限 */
    private static final int MAX_MEDIA_COUNT = 9;

    /** 图片大小上限：5MB */
    private static final long MAX_IMAGE_SIZE = 5 * 1024 * 1024;

    /** 视频大小上限：50MB */
    private static final long MAX_VIDEO_SIZE = 50 * 1024 * 1024;

    /** 允许的图片扩展名 */
    private static final Set<String> IMAGE_EXTENSIONS = Set.of("jpg", "jpeg", "png", "webp", "gif");

    /** 允许的视频扩展名 */
    private static final Set<String> VIDEO_EXTENSIONS = Set.of("mp4");

    /** OSS存储路径中的日期目录格式 */
    private static final DateTimeFormatter MEDIA_DATE_FORMAT = DateTimeFormatter.ofPattern("yyyy/MM/dd");

    /** 根据ID查询动态（不校验可见性，仅供归属校验等内部使用） */
    @Override
    public SpaceVO getSpaceById(Long id) {
        Space space = getById(id);
        return space == null ? null : toVO(space);
    }

    /** 按可见性规则查询动态详情，无权查看时视为不存在，避免泄露动态存在性 */
    @Override
    public SpaceVO getVisibleSpace(Long id, Long callerId) {
        Space space = getById(id);
        if (space == null || !isVisibleTo(space, callerId)) {
            return null;
        }
        SpaceVO vo = toVO(space);
        fillAuthor(List.of(vo));
        fillMedia(List.of(vo));
        fillLikeInfo(List.of(vo), callerId);
        return vo;
    }

    /** 查询当前用户可见的动态列表 */
    @Override
    public List<SpaceVO> listSpaces(Long callerId) {
        LambdaQueryWrapper<Space> wrapper = new LambdaQueryWrapper<>();
        applyVisibility(wrapper, callerId);
        List<SpaceVO> vos = list(wrapper).stream().map(this::toVO).collect(Collectors.toList());
        fillAuthor(vos);
        fillMedia(vos);
        fillLikeInfo(vos, callerId);
        return vos;
    }

    /** 分页查询当前用户可见的动态 */
    @Override
    public PageResult<SpaceVO> pageSpaces(SpacePageQueryDTO query, Long callerId) {
        LambdaQueryWrapper<Space> wrapper = new LambdaQueryWrapper<Space>()
                .like(StringUtils.hasText(query.getTitle()), Space::getTitle, query.getTitle())
                .eq(StringUtils.hasText(query.getCategory()), Space::getCategory, query.getCategory())
                .eq(query.getPetId() != null, Space::getPetId, query.getPetId())
                .eq(query.getUserId() != null, Space::getUserId, query.getUserId())
                .ge(query.getStartTime() != null, Space::getCreateTime, query.getStartTime())
                .le(query.getEndTime() != null, Space::getCreateTime, query.getEndTime());
        // 热度排序按点赞数倒序，相同则按发布时间兜底；默认按最新排序
        if ("hot".equalsIgnoreCase(query.getSort())) {
            wrapper.orderByDesc(Space::getLikeCount).orderByDesc(Space::getCreateTime);
        } else {
            wrapper.orderByDesc(Space::getCreateTime);
        }
        applyVisibility(wrapper, callerId);
        Page<Space> page = page(new Page<>(query.getPageNum(), query.getPageSize()), wrapper);
        List<SpaceVO> records = page.getRecords().stream().map(this::toVO).collect(Collectors.toList());
        fillAuthor(records);
        fillMedia(records);
        fillLikeInfo(records, callerId);
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), records);
    }

    /**
     * 追加可见性过滤条件：公开动态、本人动态始终可见；
     * 仅好友动态在作者是当前用户的好友时可见（好友关系双向对称）
     */
    private void applyVisibility(LambdaQueryWrapper<Space> wrapper, Long callerId) {
        List<Long> friendIds = listFriendIdsQuietly(callerId);
        wrapper.and(w -> {
            w.eq(Space::getVisibility, SpaceVisibility.PUBLIC.getCode())
                    .or().eq(Space::getUserId, callerId);
            if (!friendIds.isEmpty()) {
                w.or(w1 -> w1.eq(Space::getVisibility, SpaceVisibility.FRIENDS_ONLY.getCode())
                        .in(Space::getUserId, friendIds));
            }
        });
    }

    /** 判断动态对指定用户是否可见 */
    private boolean isVisibleTo(Space space, Long callerId) {
        // 作者本人始终可见（含仅自己）
        if (space.getUserId() != null && space.getUserId().equals(callerId)) {
            return true;
        }
        SpaceVisibility visibility = SpaceVisibility.of(space.getVisibility());
        if (visibility == SpaceVisibility.PUBLIC) {
            return true;
        }
        if (visibility == SpaceVisibility.FRIENDS_ONLY) {
            return listFriendIdsQuietly(space.getUserId()).contains(callerId);
        }
        return false;
    }

    /** 调用社交服务获取好友ID，失败时降级为空列表（仅能看到公开动态） */
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

    /** 聚合作者用户名/昵称/头像：同页作者去重后逐个查询，失败或已注销时降级展示，不阻断列表 */
    private void fillAuthor(List<SpaceVO> vos) {
        if (vos.isEmpty()) {
            return;
        }
        Map<Long, UserVO> userCache = new HashMap<>();
        for (SpaceVO vo : vos) {
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
                vo.setAuthorAvatar(author.getAvatar());
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

    /** 批量填充媒体列表：按动态ID一次查出，按 sortOrder 升序分组 */
    private void fillMedia(List<SpaceVO> vos) {
        if (vos.isEmpty()) {
            return;
        }
        List<Long> spaceIds = vos.stream().map(SpaceVO::getId).collect(Collectors.toList());
        List<SpaceMedia> mediaList = spaceMediaMapper.selectList(new LambdaQueryWrapper<SpaceMedia>()
                .in(SpaceMedia::getSpaceId, spaceIds)
                .orderByAsc(SpaceMedia::getSortOrder));
        Map<Long, List<SpaceMediaItemVO>> mediaMap = new HashMap<>();
        for (SpaceMedia media : mediaList) {
            SpaceMediaItemVO item = new SpaceMediaItemVO();
            item.setMediaType(media.getMediaType());
            item.setUrl(media.getUrl());
            mediaMap.computeIfAbsent(media.getSpaceId(), k -> new ArrayList<>()).add(item);
        }
        for (SpaceVO vo : vos) {
            vo.setMediaList(mediaMap.getOrDefault(vo.getId(), List.of()));
        }
    }

    /** 批量填充点赞数与当前用户点赞状态，调用点赞服务；失败时降级为 0/未点赞，不阻断列表 */
    private void fillLikeInfo(List<SpaceVO> vos, Long callerId) {
        if (vos.isEmpty()) {
            return;
        }
        try {
            String ids = vos.stream().map(SpaceVO::getId).map(String::valueOf)
                    .collect(Collectors.joining(","));
            Result<LikeBatchVO> result = remarkFeignClient.likeBatch(
                    LikeTargetType.SPACE.getCode(), ids, callerId);
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode() && result.getData() != null) {
                LikeBatchVO batch = result.getData();
                for (SpaceVO vo : vos) {
                    vo.setLikeCount(batch.getCounts().getOrDefault(vo.getId(), 0L));
                    vo.setLiked(batch.getLikedIds().contains(vo.getId()));
                }
                return;
            }
        } catch (Exception e) {
            log.warn("调用点赞服务获取点赞信息失败", e);
        }
        for (SpaceVO vo : vos) {
            vo.setLikeCount(0L);
            vo.setLiked(false);
        }
    }

    /** 发布动态，保存成功后为宠物发放经验奖励 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public SpaceCreateVO saveSpace(SpaceSaveDTO dto) {
        Space space = new Space();
        BeanUtils.copyProperties(dto, space, "mediaList");
        // 可见性缺省为公开，非法值拒绝
        Integer visibility = checkVisibility(dto.getVisibility());
        space.setVisibility(visibility == null ? SpaceVisibility.PUBLIC.getCode() : visibility);
        save(space);
        saveMediaList(space.getId(), dto.getMediaList());

        // 发布动态奖励宠物经验，经验发放失败仅记录日志，不影响动态保存
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
            log.warn("发布动态后为宠物发放经验失败，userId={}", dto.getUserId(), e);
        }

        SpaceCreateVO vo = new SpaceCreateVO();
        vo.setSpaceId(space.getId());
        vo.setPetExp(petExp);
        return vo;
    }

    /** 修改动态，媒体列表非空时整体替换 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean updateSpace(SpaceUpdateDTO dto) {
        Space space = getById(dto.getId());
        if (space == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "动态不存在");
        }
        checkVisibility(dto.getVisibility());
        Integer originalVisibility = space.getVisibility();
        BeanUtils.copyProperties(dto, space, "mediaList");
        // 可见性未传时保持原值（BeanUtils 会用 null 覆盖）
        if (dto.getVisibility() == null) {
            space.setVisibility(originalVisibility);
        }
        boolean updated = updateById(space);
        if (dto.getMediaList() != null) {
            // 旧媒体记录逻辑删除（OSS文件保留，避免误删），再插入新列表
            spaceMediaMapper.delete(new LambdaQueryWrapper<SpaceMedia>()
                    .eq(SpaceMedia::getSpaceId, dto.getId()));
            saveMediaList(dto.getId(), dto.getMediaList());
        }
        return updated;
    }

    /** 删除动态，同时逻辑删除其媒体记录 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean deleteSpace(Long id) {
        spaceMediaMapper.delete(new LambdaQueryWrapper<SpaceMedia>()
                .eq(SpaceMedia::getSpaceId, id));
        return removeById(id);
    }

    /** 批量保存媒体条目，按提交顺序写入排序字段 */
    private void saveMediaList(Long spaceId, List<SpaceMediaItemDTO> items) {
        if (items == null || items.isEmpty()) {
            return;
        }
        if (items.size() > MAX_MEDIA_COUNT) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "单条动态最多携带9个媒体");
        }
        for (int i = 0; i < items.size(); i++) {
            SpaceMediaItemDTO item = items.get(i);
            if (item.getMediaType() == null
                    || (item.getMediaType() != MEDIA_IMAGE && item.getMediaType() != MEDIA_VIDEO)) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "无效的媒体类型");
            }
            SpaceMedia media = new SpaceMedia();
            media.setSpaceId(spaceId);
            media.setMediaType(item.getMediaType());
            media.setUrl(item.getUrl());
            media.setSortOrder(i);
            spaceMediaMapper.insert(media);
        }
    }

    /** 上传动态媒体至OSS：图片(jpg/jpeg/png/webp/gif)≤5MB，视频(仅mp4)≤50MB */
    @Override
    public SpaceMediaUploadVO uploadMedia(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "上传文件不能为空");
        }
        String extension = getExtension(file.getOriginalFilename());
        int mediaType;
        if (IMAGE_EXTENSIONS.contains(extension)) {
            mediaType = MEDIA_IMAGE;
            if (file.getSize() > MAX_IMAGE_SIZE) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "图片大小不能超过5MB");
            }
        } else if (VIDEO_EXTENSIONS.contains(extension)) {
            mediaType = MEDIA_VIDEO;
            if (file.getSize() > MAX_VIDEO_SIZE) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "视频大小不能超过50MB");
            }
        } else {
            throw new BusinessException(ResultCode.BAD_REQUEST, "仅支持 jpg/jpeg/png/webp/gif 图片或 mp4 视频");
        }
        String objectKey = "space/" + LocalDate.now().format(MEDIA_DATE_FORMAT) + "/"
                + UUID.randomUUID().toString().replace("-", "") + "." + extension;
        SpaceMediaUploadVO vo = new SpaceMediaUploadVO();
        vo.setMediaType(mediaType);
        vo.setUrl(ossService.upload(objectKey, file));
        return vo;
    }

    /** 提取小写扩展名，无扩展名返回空串 */
    private String getExtension(String filename) {
        if (!StringUtils.hasText(filename)) {
            return "";
        }
        int dotIndex = filename.lastIndexOf('.');
        if (dotIndex < 0 || dotIndex == filename.length() - 1) {
            return "";
        }
        return filename.substring(dotIndex + 1).toLowerCase();
    }

    /** 校验可见性取值，非法拒绝；为空返回 null 由调用方决定缺省策略 */
    private Integer checkVisibility(Integer visibility) {
        if (visibility == null) {
            return null;
        }
        if (visibility < SpaceVisibility.PUBLIC.getCode() || visibility > SpaceVisibility.PRIVATE.getCode()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "无效的动态可见性");
        }
        return visibility;
    }

    /** DO 转 VO */
    private SpaceVO toVO(Space space) {
        SpaceVO vo = new SpaceVO();
        BeanUtils.copyProperties(space, vo);
        return vo;
    }
}
