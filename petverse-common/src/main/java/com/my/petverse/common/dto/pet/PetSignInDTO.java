package com.my.petverse.common.dto.pet;

import lombok.Data;

import java.io.Serializable;

/**
 * 宠物签到请求参数
 */
@Data
public class PetSignInDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户ID，由服务端从登录令牌解析写入，客户端无需传入 */
    private Long userId;
}
