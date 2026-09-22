package com.my.petverse.space.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.space.SpaceMediaItemDTO;
import com.my.petverse.common.dto.space.SpacePageQueryDTO;
import com.my.petverse.common.dto.space.SpaceSaveDTO;
import com.my.petverse.common.dto.space.SpaceUpdateDTO;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.entity.space.SpaceMedia;
import com.my.petverse.common.enums.LikeTargetType;
import com.my.petverse.common.enums.SpaceVisibility;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.SpaceCreatedMessage;
import com.my.petverse.common.mq.message.SpaceIndexMessage;
import com.my.petverse.common.oss.OssService;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.remark.LikeBatchVO;
import com.my.petverse.common.vo.space.SpaceCreateVO;
import com.my.petverse.common.vo.space.SpaceMediaItemVO;
import com.my.petverse.common.vo.space.SpaceMediaUploadVO;
import com.my.petverse.common.vo.space.SpaceVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.space.feign.RemarkFeignClient;
import com.my.petverse.space.feign.SocialFeignClient;
import com.my.petverse.space.feign.UserFeignClient;
import com.my.petverse.space.mapper.SpaceMapper;
import com.my.petverse.space.mapper.SpaceMediaMapper;
import com.my.petverse.space.search.SpaceSearchService;
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
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 宠域空间动态服务实现类
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class SpaceServiceImpl extends ServiceImpl<SpaceMapper, Space> implements SpaceService {

    private final SocialFeignClient socialFeignClient;

    private final UserFeignClient userFeignClient;

    private final RemarkFeignClient remarkFeignClient;

    private final SpaceMediaMapper spaceMediaMapper;

    private final SpaceSearchService spaceSearchService;

    private final OssService ossService;

    private final MqEventPublisher mqEventPublisher;

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
        fillLikeInfo(List.of(vo));
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
        fillLikeInfo(vos);
        return vos;
    }

    /** 分页查询当前用户可见的动态：关键词搜索优先走 Elasticsearch，不可用时降级为数据库模糊查询 */
    @Override
    public PageResult<SpaceVO> pageSpaces(SpacePageQueryDTO query, Long callerId) {
        // keyword 为新的全文检索入口，title 保留兼容旧调用
        String keyword = StringUtils.hasText(query.getKeyword()) ? query.getKeyword() : query.getTitle();
        if (StringUtils.hasText(keyword)) {
            PageResult<SpaceVO> searched = pageSpacesByKeyword(query, keyword, callerId);
            if (searched != null) {
                return searched;
            }
        }
        LambdaQueryWrapper<Space> wrapper = new LambdaQueryWrapper<Space>()
                .eq(StringUtils.hasText(query.getCategory()), Space::getCategory, query.getCategory())
                .eq(query.getPetId() != null, Space::getPetId, query.getPetId())
                .eq(query.getUserId() != null, Space::getUserId, query.getUserId())
                .ge(query.getStartTime() != null, Space::getCreateTime, query.getStartTime())
                .le(query.getEndTime() != null, Space::getCreateTime, query.getEndTime());
        // 降级路径同时匹配标题与正文，尽量贴近全文检索的召回范围
        if (StringUtils.hasText(keyword)) {
            wrapper.and(w -> w.like(Space::getTitle, keyword).or().like(Space::getContent, keyword));
        }
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
        fillLikeInfo(records);
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), records);
    }

    /**
     * 走 Elasticsearch 检索关键词：ES 只返回按相关度排序的动态ID，
     * 再以 MySQL 为准回表并复用原有聚合逻辑；检索不可用时返回 null 交由调用方降级。
     */
    private PageResult<SpaceVO> pageSpacesByKeyword(SpacePageQueryDTO query, String keyword, Long callerId) {
        if (!spaceSearchService.isAvailable()) {
            return null;
        }
        List<Long> friendIds = listFriendIdsQuietly(callerId);
        PageResult<Long> idPage = spaceSearchService.searchIds(query, keyword, friendIds, callerId);
        if (idPage == null) {
            return null;
        }
        List<Long> ids = idPage.getRecords();
        if (ids.isEmpty()) {
            return PageResult.of(idPage.getTotal(), query.getPageNum(), query.getPageSize(), List.of());
        }
        Map<Long, Space> spaceMap = listByIds(ids).stream()
                .collect(Collectors.toMap(Space::getId, Function.identity()));
        // 按 ES 返回的顺序组装，保证相关度排序不被回表打乱
        List<SpaceVO> records = ids.stream()
                .map(spaceMap::get)
                .filter(Objects::nonNull)
                .map(this::toVO)
                .collect(Collectors.toList());
        fillAuthor(records);
        fillMedia(records);
        fillLikeInfo(records);
        return PageResult.of(idPage.getTotal(), query.getPageNum(), query.getPageSize(), records);
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

    /** 聚合作者用户名/昵称/头像：整页作者去重后一次批量查询，失败或已注销时降级展示，不阻断列表 */
    private void fillAuthor(List<SpaceVO> vos) {
        if (vos.isEmpty()) {
            return;
        }
        Map<Long, UserVO> users = loadUsersQuietly(vos.stream()
                .map(SpaceVO::getUserId)
                .filter(Objects::nonNull)
                .collect(Collectors.toSet()));
        for (SpaceVO vo : vos) {
            Long authorId = vo.getUserId();
            if (authorId == null) {
                continue;
            }
            UserVO author = users.get(authorId);
            if (author != null) {
                vo.setAuthorUsername(author.getUsername());
                vo.setAuthorNickname(author.getNickname());
                vo.setAuthorAvatar(author.getAvatar());
            } else {
                vo.setAuthorUsername("用户" + authorId);
            }
        }
    }

    /** 批量查询用户资料，用户服务不可用时返回空表，由调用方降级展示 */
    private Map<Long, UserVO> loadUsersQuietly(Collection<Long> userIds) {
        if (userIds.isEmpty()) {
            return Map.of();
        }
        try {
            Result<List<UserVO>> result = userFeignClient.listByIds(new ArrayList<>(userIds));
            if (result != null && result.getCode() == ResultCode.SUCCESS.getCode() && result.getData() != null) {
                return result.getData().stream()
                        .filter(user -> user.getId() != null)
                        .collect(Collectors.toMap(UserVO::getId, Function.identity(), (a, b) -> a));
            }
        } catch (Exception e) {
            log.warn("批量查询用户资料失败，用户数={}", userIds.size(), e);
        }
        return Map.of();
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

    /** 批量填充点赞数与当前用户点赞状态，调用点赞服务；失败时降级为 0/未点赞，不阻断列表。
     * 当前用户身份由 Feign 拦截器从 UserContext 自动透传 */
    private void fillLikeInfo(List<SpaceVO> vos) {
        if (vos.isEmpty()) {
            return;
        }
        try {
            String ids = vos.stream().map(SpaceVO::getId).map(String::valueOf)
                    .collect(Collectors.joining(","));
            Result<LikeBatchVO> result = remarkFeignClient.likeBatch(
                    LikeTargetType.SPACE.getCode(), ids);
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

    /** 发布动态：落库后以事件驱动异步发放宠物经验与维护检索索引 */
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
        // 事务提交后发布事件：pet-service 消费发放宠物经验，索引消费者维护 ES 检索索引，
        // 下游失败由 RocketMQ 重试兼容，不阻断发布主流程（经验奖励改为异步，不再同步返回）
        mqEventPublisher.publishAfterCommit(MqTopics.SPACE_EVENT, MqTopics.TAG_SPACE_CREATED,
                new SpaceCreatedMessage(space.getId(), dto.getUserId()),
                "space-created:" + space.getId());
        mqEventPublisher.publishAfterCommit(MqTopics.SPACE_EVENT, MqTopics.TAG_SPACE_INDEX_UPSERT,
                new SpaceIndexMessage(space.getId()), "space-index:" + space.getId());

        SpaceCreateVO vo = new SpaceCreateVO();
        vo.setSpaceId(space.getId());
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
        // 事务提交后发事件，由索引消费者异步刷新 ES 检索索引（回表读取最新数据）
        mqEventPublisher.publishAfterCommit(MqTopics.SPACE_EVENT, MqTopics.TAG_SPACE_INDEX_UPSERT,
                new SpaceIndexMessage(space.getId()), "space-index:" + space.getId());
        return updated;
    }

    /** 删除动态，同时逻辑删除其媒体记录 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public boolean deleteSpace(Long id) {
        spaceMediaMapper.delete(new LambdaQueryWrapper<SpaceMedia>()
                .eq(SpaceMedia::getSpaceId, id));
        boolean removed = removeById(id);
        // 事务提交后发事件，由索引消费者异步删除 ES 检索索引
        mqEventPublisher.publishAfterCommit(MqTopics.SPACE_EVENT, MqTopics.TAG_SPACE_INDEX_REMOVE,
                new SpaceIndexMessage(id), "space-index:" + id);
        return removed;
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
