package com.my.petverse.social.feign;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.user.UserVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.List;

/**
 * 用户服务 Feign 客户端，用于聚合好友/申请人的用户资料
 */
@FeignClient(name = "user-service")
public interface UserFeignClient {

    /** 根据用户ID查询用户信息 */
    @GetMapping("/user/{id}")
    Result<UserVO> getUserById(@PathVariable("id") Long id);

    /** 批量查询用户信息，好友/申请列表一次请求完成聚合 */
    @GetMapping("/user/internal/batch")
    Result<List<UserVO>> listByIds(@RequestParam("ids") List<Long> ids);
}
