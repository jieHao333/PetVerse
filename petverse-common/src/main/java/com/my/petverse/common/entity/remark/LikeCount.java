package com.my.petverse.common.entity.remark;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 点赞计数快照实体，对应数据库表 like_count
 * 联合主键 (target_type, target_id)，Redis 冷数据回填用
 */
@Data
@TableName("like_count")
public class LikeCount implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 点赞对象类型，见 LikeTargetType（联合主键，无自增主键） */
    @TableId(type = IdType.INPUT)
    private Integer targetType;

    /** 被点赞对象ID */
    private Long targetId;

    /** 点赞数快照 */
    private Integer count;

    /** 最近同步时间 */
    private LocalDateTime updateTime;
}
