package com.my.petverse.common.dto.remark;

import com.my.petverse.common.dto.base.BasePageQuery;
import jakarta.validation.constraints.NotNull;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 评论分页查询参数（按对象维度查询，时间正序展示互动过程）
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class CommentPageQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 评论对象类型：0-圈子动态 */
    @NotNull(message = "评论对象类型不能为空")
    private Integer targetType;

    /** 被评论对象ID */
    @NotNull(message = "评论对象ID不能为空")
    private Long targetId;
}
