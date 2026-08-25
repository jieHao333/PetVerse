package com.my.petverse.common.entity.remark;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 点赞记录实体，对应数据库表 like_record
 * 取消点赞采用物理删除，避免唯一键与重新点赞冲突，故不继承 BaseEntity
 */
@Data
@TableName("like_record")
public class LikeRecord implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 主键，雪花算法自动生成 */
    @TableId(type = IdType.ASSIGN_ID)
    private Long id;

    /** 点赞对象类型，见 LikeTargetType */
    private Integer targetType;

    /** 被点赞对象ID */
    private Long targetId;

    /** 点赞用户ID */
    private Long userId;

    /** 点赞时间，插入时自动填充 */
    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createTime;
}
