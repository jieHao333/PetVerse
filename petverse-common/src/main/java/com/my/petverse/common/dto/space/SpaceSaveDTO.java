package com.my.petverse.common.dto.space;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;
import java.util.List;

/**
 * 发布宠域空间动态请求参数
 */
@Data
public class SpaceSaveDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 动态标题（可选） */
    @Size(max = 100, message = "标题不能超过100个字符")
    private String title;

    /** 动态内容 */
    @NotBlank(message = "内容不能为空")
    private String content;

    /** 动态分类 */
    @Size(max = 50, message = "分类不能超过50个字符")
    private String category;

    /** 关联的宠物ID，可为空 */
    private Long petId;

    /** 所属用户ID */
    @NotNull(message = "用户ID不能为空")
    private Long userId;

    /** 可见性：0-公开 1-仅好友 2-仅自己，缺省为公开 */
    private Integer visibility;

    /** 媒体列表（图片/视频），最多 9 个 */
    @Valid
    @Size(max = 9, message = "单条动态最多携带9个媒体")
    private List<SpaceMediaItemDTO> mediaList;
}
