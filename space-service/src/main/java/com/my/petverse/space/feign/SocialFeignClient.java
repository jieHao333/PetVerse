package com.my.petverse.space.feign;

import com.my.petverse.common.result.Result;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

import java.util.List;

/**
 * 社交服务 Feign 客户端，通过 Nacos 服务名直连 social-service
 * 用于动态可见性判断（仅好友可见需要好友关系数据）
 */
@FeignClient(name = "social-service")
public interface SocialFeignClient {

    /** 查询指定用户的好友用户ID列表 */
    @GetMapping("/social/friend/internal/friend-ids/{userId}")
    Result<List<Long>> listFriendIds(@PathVariable("userId") Long userId);
}
