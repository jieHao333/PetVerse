package com.my.petverse.space.feign;

import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.remark.LikeBatchVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.Map;

/**
 * 点赞服务 Feign 客户端，通过 Nacos 服务名直连 remark-service
 * 用于动态列表/详情聚合点赞数与当前用户点赞状态；
 * 当前用户身份由公共模块的 Feign 拦截器自动透传，无需手动携带
 */
@FeignClient(name = "remark-service")
public interface RemarkFeignClient {

    /** 批量查询点赞数与指定用户的点赞状态（动态对象 targetType=0） */
    @GetMapping("/remark/like/batch")
    Result<LikeBatchVO> likeBatch(@RequestParam("targetType") Integer targetType,
                                  @RequestParam("targetIds") String targetIds);

    /** 批量查询点赞数（仅计数），供定时任务同步热度冗余列 */
    @GetMapping("/remark/like/internal/counts")
    Result<Map<Long, Long>> counts(@RequestParam("targetType") Integer targetType,
                                   @RequestParam("targetIds") String targetIds);
}
