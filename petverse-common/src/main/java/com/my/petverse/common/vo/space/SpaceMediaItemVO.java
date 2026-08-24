package com.my.petverse.common.vo.space;

import lombok.Data;

import java.io.Serializable;

/**
 * 动态媒体条目返回实体
 */
@Data
public class SpaceMediaItemVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 媒体类型：0-图片 1-视频 */
    private Integer mediaType;

    /** 媒体OSS地址 */
    private String url;
}
