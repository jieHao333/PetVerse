package com.my.petverse.shop.feign;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.user.UserVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestParam;

/**
 * 用户服务 Feign 客户端，通过 Nacos 服务名直连 user-service
 * 用于入驻审批通过后升级用户角色，以及管理端申请列表聚合申请人信息
 */
@FeignClient(name = "user-service")
public interface UserFeignClient {

    /** 根据用户ID查询用户信息 */
    @GetMapping("/user/{id}")
    Result<UserVO> getUserById(@PathVariable("id") Long id);

    /** 升级用户角色（内部接口，仅供服务间调用） */
    @PutMapping("/user/internal/role")
    Result<Boolean> upgradeRole(@RequestParam("userId") Long userId,
                                @RequestParam("role") String role);
}
