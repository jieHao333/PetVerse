package com.my.petverse.social.support;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.social.feign.UserFeignClient;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 用户资料批量装载器
 *
 * 好友与聊天都要按用户ID补齐昵称/头像等资料，统一在此收口：一次 Feign 请求聚合整批用户，
 * 用户服务不可用或用户已注销时返回空表而非抛错，由调用方按ID降级展示，保证关系与消息记录不丢失。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class UserProfileLoader {

    private final UserFeignClient userFeignClient;

    /** 批量查询用户资料，返回 用户ID -> 资料；异常或空结果时返回空表 */
    public Map<Long, UserVO> loadQuietly(Collection<Long> userIds) {
        List<Long> ids = userIds.stream()
                .filter(Objects::nonNull)
                .distinct()
                .collect(Collectors.toList());
        if (ids.isEmpty()) {
            return Map.of();
        }
        try {
            Result<List<UserVO>> result = userFeignClient.listByIds(ids);
            if (result == null) {
                log.warn("用户服务返回空结果，用户数={}", ids.size());
                return Map.of();
            }
            if (result.getCode() != ResultCode.SUCCESS.getCode()) {
                log.warn("用户服务返回异常状态码，用户数={}, code={}, msg={}", ids.size(), result.getCode(), result.getMsg());
                return Map.of();
            }
            if (result.getData() == null) {
                return Map.of();
            }
            return result.getData().stream()
                    .filter(user -> user.getId() != null)
                    .collect(Collectors.toMap(UserVO::getId, Function.identity(), (a, b) -> a));
        } catch (Exception e) {
            log.warn("批量查询用户资料失败，用户数={}", ids.size(), e);
            return Map.of();
        }
    }
}
