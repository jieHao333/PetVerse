package com.my.petverse.common.dto.user;

import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 修改用户资料请求参数
 */
@Data
public class UserUpdateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID */
    private Long id;

    /** 昵称，最多30字符 */
    @Size(max = 30, message = "昵称不能超过30个字符")
    private String nickname;

    /** 头像地址 */
    private String avatar;

    /** 原密码，修改密码时必填 */
    private String oldPassword;

    /** 新密码，6-32位 */
    @Size(min = 6, max = 32, message = "密码长度需为6-32位")
    private String newPassword;
}
