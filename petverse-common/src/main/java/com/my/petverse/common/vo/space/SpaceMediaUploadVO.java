package com.my.petverse.common.vo.space;

import lombok.Data;

import java.io.Serializable;

/**
 * 动态媒体上传结果返回实体
 */
@Data
public class SpaceMediaUploadVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 媒体类型：0-图片 1-视频 */
    private Integer mediaType;

    /** 媒体OSS公网访问地址 */
    private String url;
}
