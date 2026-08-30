package com.my.petverse.common.config;

import com.my.petverse.common.context.UserContext;
import feign.RequestInterceptor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.StringUtils;

/**
 * Feign 身份透传配置
 * 服务间调用不走网关，把当前请求线程中的用户身份以内部请求头转发给下游服务；
 * 下游服务的 UserContextInterceptor 在无令牌时回退解析该内部头。
 * 网关会剥离外部请求携带的 X-User-Id/X-User-Role，故此头仅可能来自服务内部，不可被客户端伪造
 */
@Configuration
public class FeignUserContextConfig {

    @Bean
    public RequestInterceptor userContextFeignInterceptor() {
        return template -> {
            Long userId = UserContext.peekUserId();
            if (userId == null) {
                // 定时任务等无登录态的调用不透传身份
                return;
            }
            template.header("X-User-Id", String.valueOf(userId));
            String role = UserContext.getRole();
            if (StringUtils.hasText(role)) {
                template.header("X-User-Role", role);
            }
        };
    }
}
