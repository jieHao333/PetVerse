package com.my.petverse.space.feign;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.user.UserVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

/**
 * 用户服务 Feign 客户端，通过 Nacos 服务名直连 user-service
 * 用于宠域空间动态列表聚合作者信息
 */
@FeignClient(name = "user-service")
public interface UserFeignClient {

    /** 根据用户ID查询用户信息 */
    @GetMapping("/user/{id}")
    Result<UserVO> getUserById(@PathVariable("id") Long id);
}
