package com.my.petverse.data.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.data.DataBehavior;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/**
 * DWD 行为明细 Mapper，附带微批聚合查询（商品统计 / 用户行为 / 物种亲和度 / 反馈样本 / 按日指标）。
 * 聚合以 SQL 分组完成，返回列别名即 Map 键，数值由服务层安全转换。
 */
@Mapper
public interface DataBehaviorMapper extends BaseMapper<DataBehavior> {

    /** 按商品聚合曝光/点击/下单/评价次数 */
    @Select("SELECT target_id AS productId, "
            + "SUM(event_type = 'exposure') AS exposureCnt, "
            + "SUM(event_type = 'click') AS clickCnt, "
            + "SUM(event_type = 'order') AS orderCnt, "
            + "SUM(event_type = 'review') AS reviewCnt "
            + "FROM data_behavior WHERE deleted = 0 AND target_type = 'product' AND target_id IS NOT NULL "
            + "GROUP BY target_id")
    List<Map<String, Object>> aggregateProductStats();

    /** 按用户聚合行为计数与最近活跃时间 */
    @Select("SELECT user_id AS userId, "
            + "SUM(event_type = 'exposure') AS exposureCnt, "
            + "SUM(event_type = 'click') AS clickCnt, "
            + "SUM(event_type = 'search') AS searchCnt, "
            + "SUM(event_type = 'order') AS orderCnt, "
            + "SUM(event_type = 'ai_feedback' AND feedback = 1) AS aiUp, "
            + "SUM(event_type = 'ai_feedback' AND feedback = -1) AS aiDown, "
            + "COUNT(*) AS totalCnt, "
            + "MAX(event_time) AS lastActive "
            + "FROM data_behavior WHERE deleted = 0 AND user_id IS NOT NULL GROUP BY user_id")
    List<Map<String, Object>> aggregateUserBehavior();

    /** 按用户+物种聚合亲和度权重（点击计2、搜索计1） */
    @Select("SELECT user_id AS userId, species, "
            + "SUM(CASE WHEN event_type = 'click' THEN 2 WHEN event_type = 'search' THEN 1 ELSE 0 END) AS weight "
            + "FROM data_behavior WHERE deleted = 0 AND user_id IS NOT NULL AND species IS NOT NULL AND species <> '' "
            + "AND event_type IN ('click','search') GROUP BY user_id, species")
    List<Map<String, Object>> aggregateSpeciesAffinity();

    /** 按 用户+场景+目标 聚合当日曝光⋈点击，生成反馈样本 */
    @Select("SELECT user_id AS userId, scene, target_type AS targetType, target_id AS targetId, "
            + "MAX(position) AS position, "
            + "MAX(event_type = 'exposure') AS exposed, "
            + "MAX(event_type = 'click') AS clicked, "
            + "MAX(event_time) AS eventTime "
            + "FROM data_behavior WHERE deleted = 0 AND user_id IS NOT NULL AND target_id IS NOT NULL "
            + "AND event_type IN ('exposure','click') AND DATE(event_time) = #{date} "
            + "GROUP BY user_id, scene, target_type, target_id")
    List<Map<String, Object>> aggregateSamples(@Param("date") LocalDate date);

    /** 当日活跃用户数（DAU） */
    @Select("SELECT COUNT(DISTINCT user_id) FROM data_behavior "
            + "WHERE deleted = 0 AND user_id IS NOT NULL AND DATE(event_time) = #{date}")
    Long countActiveUsers(@Param("date") LocalDate date);

    /** 当日推荐/AI/搜索等指标聚合 */
    @Select("SELECT "
            + "SUM(event_type = 'exposure' AND scene IN ('home','shop')) AS recExposure, "
            + "SUM(event_type = 'click' AND scene IN ('home','shop')) AS recClick, "
            + "SUM(event_type = 'ai_feedback' AND feedback = 1) AS aiUp, "
            + "SUM(event_type = 'ai_feedback' AND feedback = -1) AS aiDown, "
            + "SUM(event_type = 'search') AS searchCnt, "
            + "SUM(event_type = 'page_view') AS pageView, "
            + "COUNT(*) AS behaviorCount "
            + "FROM data_behavior WHERE deleted = 0 AND DATE(event_time) = #{date}")
    Map<String, Object> aggregateDailyMetrics(@Param("date") LocalDate date);
}