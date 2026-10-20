package com.my.petverse.shop.feign;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.user.UserVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.List;

/**
 * 用户服务 Feign 客户端，通过 Nacos 服务名直连 user-service，
 * 用于管理端申请列表聚合申请人信息与撤销冷静期结束后的角色降级兜底
 */
@FeignClient(name = "user-service")
public interface UserFeignClient {

    /** 根据用户ID查询用户信息 */
    @GetMapping("/user/{id}")
    Result<UserVO> getUserById(@PathVariable("id") Long id);

    /** 批量查询用户信息，整页申请人一次请求完成聚合 */
    @GetMapping("/user/internal/batch")
    Result<List<UserVO>> listByIds(@RequestParam("ids") List<Long> ids);

    /** 更新用户角色（内部接口，用于商家身份撤销后降级为普通用户） */
    @PutMapping("/user/internal/role")
    Result<Boolean> upgradeRole(@RequestParam("userId") Long userId, @RequestParam("role") String role);
}
