package com.my.petverse.common.vo.user;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 用户信息返回实体（不包含密码）
 */
@Data
public class UserVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    private Long id;

    /** 登录用户名 */
    private String username;

    /** 昵称 */
    private String nickname;

    /** 头像地址 */
    private String avatar;

    /** 账号状态：1-正常 0-禁用 */
    private Integer status;

    /** 角色：USER-普通用户 MERCHANT-商家 ADMIN-管理员 */
    private String role;

    /** 创建时间 */
    private LocalDateTime createTime;
}
