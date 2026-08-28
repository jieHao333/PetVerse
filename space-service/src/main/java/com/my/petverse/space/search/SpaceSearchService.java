package com.my.petverse.space.search;

import com.my.petverse.common.dto.space.SpacePageQueryDTO;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.result.PageResult;

import java.util.List;

/**
 * 动态全文检索服务：Elasticsearch 只作为检索索引，展示数据仍以 MySQL 为准。
 * 所有方法在 ES 不可用时均不抛异常，由调用方降级为数据库查询。
 */
public interface SpaceSearchService {

    /** ES 检索是否可用（配置开启且当前未处于降级窗口内） */
    boolean isAvailable();

    /** 新增或更新单条动态的索引 */
    void indexSpace(Space space);

    /** 删除单条动态的索引 */
    void removeSpace(Long spaceId);

    /** 局部更新索引中的点赞数，保证热度排序与数据库一致 */
    void updateLikeCount(Long spaceId, int likeCount);

    /** 索引不存在时创建索引并全量灌入库中已有数据，返回灌入条数 */
    long initIndexIfAbsent();

    /**
     * 按关键词检索可见动态，返回按相关度（或热度）排序的动态ID分页结果。
     *
     * @param friendIds 当前用户的好友ID列表，用于复刻数据库侧的可见性规则
     * @return ES 不可用或检索失败时返回 null，调用方需降级为数据库查询
     */
    PageResult<Long> searchIds(SpacePageQueryDTO query, String keyword, List<Long> friendIds, Long callerId);
}
