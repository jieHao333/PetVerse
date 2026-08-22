package com.my.petverse.common.entity.user;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDateTime;

/**
 * 用户实体，对应数据库表 user
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("user")
public class User extends BaseEntity {

    /** 登录用户名（唯一） */
    private String username;

    /** 登录密码（BCrypt 加密存储，禁止明文） */
    private String password;

    /** 昵称，社交展示名 */
    private String nickname;

    /** 头像地址 */
    private String avatar;

    /** 账号状态：1-正常 0-禁用 */
    private Integer status;

    /** 最近登录时间 */
    private LocalDateTime lastLoginTime;
}
