package com.my.petverse.common.vo.user;

import lombok.Data;

import java.io.Serializable;

/**
 * 登录结果返回实体
 */
@Data
public class LoginVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** JWT 访问令牌，后续请求放在 Authorization: Bearer <token> 头中 */
    private String token;

    /** 用户信息 */
    private UserVO user;
}
