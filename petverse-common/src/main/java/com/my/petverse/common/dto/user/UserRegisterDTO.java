package com.my.petverse.common.dto.user;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 用户注册请求参数
 */
@Data
public class UserRegisterDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户名，4-20位字母/数字/下划线 */
    @NotBlank(message = "用户名不能为空")
    @Pattern(regexp = "^[a-zA-Z0-9_]{4,20}$", message = "用户名需为4-20位字母、数字或下划线")
    private String username;

    /** 密码，6-32位 */
    @NotBlank(message = "密码不能为空")
    @Size(min = 6, max = 32, message = "密码长度需为6-32位")
    private String password;

    /** 昵称，可选，最多30字符 */
    @Size(max = 30, message = "昵称不能超过30个字符")
    private String nickname;
}
