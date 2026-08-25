package com.my.petverse.common.entity.space;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 宠域空间动态实体，对应数据库表 space
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("space")
public class Space extends BaseEntity {

    /** 动态标题（可选） */
    private String title;

    /** 动态内容 */
    private String content;

    /** 动态分类 */
    private String category;

    /** 关联的宠物ID，可为空 */
    private Long petId;

    /** 所属用户ID */
    private Long userId;

    /** 可见性：0-公开 1-仅好友 2-仅自己 */
    private Integer visibility;

    /** 点赞数（由点赞服务定时同步，用于热度排序） */
    private Integer likeCount;
}
