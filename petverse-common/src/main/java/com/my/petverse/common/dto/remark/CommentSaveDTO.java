package com.my.petverse.common.dto.remark;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 发表评论参数（所有登录用户均可评论，无需其他资格）
 */
@Data
public class CommentSaveDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 评论对象类型，见 CommentTargetType：0-圈子动态 */
    @NotNull(message = "评论对象类型不能为空")
    private Integer targetType;

    /** 被评论对象ID */
    @NotNull(message = "评论对象ID不能为空")
    private Long targetId;

    /** 评论内容 */
    @NotBlank(message = "评论内容不能为空")
    @Size(max = 500, message = "评论内容不能超过500字")
    private String content;

    /** 被回复人用户ID（选填，回复某条评论时传入） */
    private Long replyUserId;
}
