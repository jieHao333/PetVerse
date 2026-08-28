package com.my.petverse.space.search;

import lombok.RequiredArgsConstructor;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

/**
 * 启动时初始化动态索引：索引不存在则建索引并全量灌入库中数据。
 * ES 不可用时初始化内部已做异常兜底，不影响服务启动。
 */
@Component
@RequiredArgsConstructor
public class SpaceSearchIndexInitializer implements ApplicationRunner {

    private final SpaceSearchService spaceSearchService;

    @Override
    public void run(ApplicationArguments args) {
        spaceSearchService.initIndexIfAbsent();
    }
}
