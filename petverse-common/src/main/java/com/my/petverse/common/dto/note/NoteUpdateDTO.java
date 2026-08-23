package com.my.petverse.common.dto.note;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 修改笔记请求参数
 */
@Data
public class NoteUpdateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 笔记ID */
    @NotNull(message = "笔记ID不能为空")
    private Long id;

    /** 笔记标题 */
    @NotBlank(message = "标题不能为空")
    @Size(max = 100, message = "标题不能超过100个字符")
    private String title;

    /** 笔记内容 */
    @NotBlank(message = "内容不能为空")
    private String content;

    /** 笔记分类 */
    @Size(max = 50, message = "分类不能超过50个字符")
    private String category;

    /** 关联的宠物ID，可为空 */
    private Long petId;

    /** 可见性：0-公开 1-仅好友 2-仅自己，为空时不修改 */
    private Integer visibility;
}
