package com.my.petverse.common.dto.space;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 动态媒体条目（保存/更新时由前端提交，url 来自媒体上传接口）
 */
@Data
public class SpaceMediaItemDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 媒体类型：0-图片 1-视频 */
    @NotNull(message = "媒体类型不能为空")
    private Integer mediaType;

    /** 媒体OSS地址 */
    @NotBlank(message = "媒体地址不能为空")
    private String url;
}
