package com.my.petverse.common.entity.space;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 动态媒体实体，对应数据库表 space_media
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("space_media")
public class SpaceMedia extends BaseEntity {

    /** 所属动态ID */
    private Long spaceId;

    /** 媒体类型：0-图片 1-视频 */
    private Integer mediaType;

    /** 媒体OSS地址 */
    private String url;

    /** 展示顺序，从 0 开始 */
    private Integer sortOrder;
}
