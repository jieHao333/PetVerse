package com.my.petverse.space.controller;

import com.my.petverse.common.dto.space.SpacePageQueryDTO;
import com.my.petverse.common.dto.space.SpaceSaveDTO;
import com.my.petverse.common.dto.space.SpaceUpdateDTO;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.space.SpaceCreateVO;
import com.my.petverse.common.vo.space.SpaceMediaUploadVO;
import com.my.petverse.common.vo.space.SpaceVO;
import com.my.petverse.space.service.SpaceService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * 宠域空间动态接口控制器
 */
@RestController
@RequestMapping("/space")
@RequiredArgsConstructor
public class SpaceController {

    private final SpaceService spaceService;

    /** 根据ID查询动态，按当前用户可见性过滤，无权查看时视为不存在 */
    @GetMapping("/{id}")
    public Result<SpaceVO> getById(@PathVariable("id") Long id,
                                   @RequestHeader("X-User-Id") Long userId) {
        return Result.success(spaceService.getVisibleSpace(id, userId));
    }

    /** 查询当前用户可见的动态列表 */
    @GetMapping("/list")
    public Result<List<SpaceVO>> list(@RequestHeader("X-User-Id") Long userId) {
        return Result.success(spaceService.listSpaces(userId));
    }

    /** 分页查询当前用户可见的动态 */
    @GetMapping("/page")
    public Result<PageResult<SpaceVO>> page(SpacePageQueryDTO query,
                                            @RequestHeader("X-User-Id") Long userId) {
        return Result.success(spaceService.pageSpaces(query, userId));
    }

    /** 发布动态，同时为宠物发放经验奖励 */
    @PostMapping
    public Result<SpaceCreateVO> save(@RequestBody @Valid SpaceSaveDTO dto) {
        return Result.success(spaceService.saveSpace(dto));
    }

    /** 修改动态，仅作者本人可操作 */
    @PutMapping
    public Result<Boolean> update(@RequestHeader("X-User-Id") Long userId,
                                  @RequestBody @Valid SpaceUpdateDTO dto) {
        checkOwnership(dto.getId(), userId);
        return Result.success(spaceService.updateSpace(dto));
    }

    /** 删除动态，仅作者本人可操作 */
    @DeleteMapping("/{id}")
    public Result<Boolean> delete(@PathVariable("id") Long id,
                                  @RequestHeader("X-User-Id") Long userId) {
        checkOwnership(id, userId);
        return Result.success(spaceService.deleteSpace(id));
    }

    /** 上传动态媒体（图片≤5MB/视频mp4≤50MB），返回媒体类型与OSS公网地址 */
    @PostMapping("/media")
    public Result<SpaceMediaUploadVO> uploadMedia(@RequestParam("file") MultipartFile file) {
        return Result.success(spaceService.uploadMedia(file));
    }

    /** 校验动态归属：动态不存在或不属于当前用户时拒绝操作 */
    private void checkOwnership(Long spaceId, Long userId) {
        SpaceVO space = spaceService.getSpaceById(spaceId);
        if (space == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "动态不存在");
        }
        if (space.getUserId() == null || !space.getUserId().equals(userId)) {
            throw new BusinessException(ResultCode.FORBIDDEN, "只能操作自己发布的动态");
        }
    }
}
