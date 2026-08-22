package com.my.petverse.common.entity.social;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 好友关系实体，对应数据库表 friendship
 * 每对好友关系存储双向两条记录，便于按用户维度直接查询
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("friendship")
public class Friendship extends BaseEntity {

    /** 用户ID */
    private Long userId;

    /** 好友的用户ID */
    private Long friendUserId;
}
