package com.my.petverse.remark.service;

import com.my.petverse.common.vo.remark.LikeBatchVO;

import java.util.List;
import java.util.Map;

/**
 * 通用点赞服务接口，对象以 (targetType, targetId) 定位
 */
public interface LikeService {

    /** 点赞（幂等，重复点赞返回成功） */
    void like(Integer targetType, Long targetId, Long userId);

    /** 取消点赞（幂等，未点赞时取消同样返回成功） */
    void unlike(Integer targetType, Long targetId, Long userId);

    /** 批量查询点赞数与当前用户是否点赞，Redis 冷数据自动回填 */
    LikeBatchVO batchQuery(Integer targetType, List<Long> targetIds, Long userId);

    /** 批量查询点赞数（仅计数，供其他服务内部调用） */
    Map<Long, Long> batchCounts(Integer targetType, List<Long> targetIds);
}
