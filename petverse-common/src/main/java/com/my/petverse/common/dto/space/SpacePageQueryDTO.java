package com.my.petverse.common.dto.space;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.springframework.format.annotation.DateTimeFormat;

import java.time.LocalDateTime;

/**
 * 宠域空间动态分页查询参数
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class SpacePageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 动态标题（模糊匹配） */
    private String title;

    /** 动态分类（精确匹配） */
    private String category;

    /** 关联的宠物ID */
    private Long petId;

    /** 所属用户ID */
    private Long userId;

    /** 发布时间起（含），格式 yyyy-MM-dd HH:mm:ss */
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime startTime;

    /** 发布时间止（含），格式 yyyy-MM-dd HH:mm:ss */
    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private LocalDateTime endTime;
}
