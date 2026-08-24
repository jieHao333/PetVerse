package com.my.petverse.space.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.space.SpacePageQueryDTO;
import com.my.petverse.common.dto.space.SpaceSaveDTO;
import com.my.petverse.common.dto.space.SpaceUpdateDTO;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.space.SpaceCreateVO;
import com.my.petverse.common.vo.space.SpaceMediaUploadVO;
import com.my.petverse.common.vo.space.SpaceVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * 宠域空间动态服务接口
 */
public interface SpaceService extends IService<Space> {

    /** 根据ID查询动态（不校验可见性，仅供归属校验等内部使用） */
    SpaceVO getSpaceById(Long id);

    /** 按可见性规则查询动态详情，无权查看或不存在时返回 null */
    SpaceVO getVisibleSpace(Long id, Long callerId);

    /** 查询当前用户可见的动态列表 */
    List<SpaceVO> listSpaces(Long callerId);

    /** 分页查询当前用户可见的动态 */
    PageResult<SpaceVO> pageSpaces(SpacePageQueryDTO query, Long callerId);

    /** 发布动态并为宠物发放经验奖励，返回创建结果 */
    SpaceCreateVO saveSpace(SpaceSaveDTO dto);

    /** 修改动态 */
    boolean updateSpace(SpaceUpdateDTO dto);

    /** 删除动态，同时逻辑删除其媒体记录 */
    boolean deleteSpace(Long id);

    /** 上传动态媒体（图片/视频）至OSS，返回媒体类型与公网地址 */
    SpaceMediaUploadVO uploadMedia(MultipartFile file);
}
