package com.my.petverse.common.vo.space;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 宠域空间动态返回实体
 */
@Data
public class SpaceVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 动态ID */
    private Long id;

    /** 动态标题（可选） */
    private String title;

    /** 动态内容 */
    private String content;

    /** 动态分类 */
    private String category;

    /** 关联的宠物ID */
    private Long petId;

    /** 所属用户ID */
    private Long userId;

    /** 作者用户名（聚合自用户服务，服务不可用时降级为用户+ID） */
    private String authorUsername;

    /** 作者昵称（聚合自用户服务） */
    private String authorNickname;

    /** 作者头像（聚合自用户服务） */
    private String authorAvatar;

    /** 可见性：0-公开 1-仅好友 2-仅自己 */
    private Integer visibility;

    /** 点赞数（聚合自点赞服务，服务不可用时降级为0） */
    private Long likeCount;

    /** 当前用户是否已点赞（聚合自点赞服务） */
    private Boolean liked;

    /** 媒体列表，按排序字段升序 */
    private List<SpaceMediaItemVO> mediaList;

    /** 创建时间 */
    private LocalDateTime createTime;
}
