package com.my.petverse.space.search;

import co.elastic.clients.elasticsearch._types.FieldValue;
import co.elastic.clients.elasticsearch._types.SortOptions;
import co.elastic.clients.elasticsearch._types.SortOrder;
import co.elastic.clients.elasticsearch._types.query_dsl.BoolQuery;
import co.elastic.clients.elasticsearch._types.query_dsl.TextQueryType;
import co.elastic.clients.json.JsonData;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.my.petverse.common.document.space.SpaceDoc;
import com.my.petverse.common.dto.space.SpacePageQueryDTO;
import com.my.petverse.common.entity.space.Space;
import com.my.petverse.common.enums.SpaceVisibility;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.space.mapper.SpaceMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.elasticsearch.client.elc.NativeQuery;
import org.springframework.data.elasticsearch.core.ElasticsearchOperations;
import org.springframework.data.elasticsearch.core.SearchHit;
import org.springframework.data.elasticsearch.core.SearchHits;
import org.springframework.data.elasticsearch.core.IndexOperations;
import org.springframework.data.elasticsearch.core.document.Document;
import org.springframework.data.elasticsearch.core.query.UpdateQuery;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 动态全文检索服务实现：写入时双写索引，检索时只取文档ID再回表。
 * ES 异常一律降级处理并进入短暂的降级窗口，避免每个请求都等待连接超时。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class SpaceSearchServiceImpl implements SpaceSearchService {

    private final ElasticsearchOperations elasticsearchOperations;

    private final SpaceMapper spaceMapper;

    /** 搜索总开关，关闭后全部走数据库模糊查询 */
    @Value("${petverse.search.enabled:true}")
    private boolean searchEnabled;

    /** ES 异常后的降级窗口时长 */
    private static final long DEGRADE_WINDOW_MILLIS = 60_000L;

    /** 全量重建索引时的每批条数 */
    private static final int REINDEX_BATCH_SIZE = 500;

    /** 降级截止时间戳，该时刻之前不再访问 ES */
    private volatile long degradedUntil = 0L;

    @Override
    public boolean isAvailable() {
        return searchEnabled && System.currentTimeMillis() >= degradedUntil;
    }

    @Override
    public void indexSpace(Space space) {
        if (!searchEnabled || space == null) {
            return;
        }
        try {
            elasticsearchOperations.save(toDoc(space));
        } catch (Exception e) {
            markDegraded("写入动态索引失败，spaceId=" + space.getId(), e);
        }
    }

    @Override
    public void removeSpace(Long spaceId) {
        if (!searchEnabled || spaceId == null) {
            return;
        }
        try {
            elasticsearchOperations.delete(String.valueOf(spaceId), SpaceDoc.class);
        } catch (Exception e) {
            markDegraded("删除动态索引失败，spaceId=" + spaceId, e);
        }
    }

    @Override
    public void updateLikeCount(Long spaceId, int likeCount) {
        if (!isAvailable() || spaceId == null) {
            return;
        }
        try {
            // 局部更新，避免整文档覆盖导致正文等字段丢失
            UpdateQuery updateQuery = UpdateQuery.builder(String.valueOf(spaceId))
                    .withDocument(Document.from(Map.of("likeCount", likeCount)))
                    .build();
            elasticsearchOperations.update(updateQuery,
                    elasticsearchOperations.getIndexCoordinatesFor(SpaceDoc.class));
        } catch (Exception e) {
            markDegraded("更新动态索引点赞数失败，spaceId=" + spaceId, e);
        }
    }

    @Override
    public long initIndexIfAbsent() {
        if (!searchEnabled) {
            log.info("动态搜索已关闭，跳过 Elasticsearch 索引初始化");
            return 0L;
        }
        try {
            IndexOperations indexOperations = elasticsearchOperations.indexOps(SpaceDoc.class);
            if (indexOperations.exists()) {
                return 0L;
            }
            indexOperations.createWithMapping();
            long total = reindexAll();
            log.info("动态索引创建完成，已灌入 {} 条数据", total);
            return total;
        } catch (Exception e) {
            markDegraded("动态索引初始化失败，搜索将降级为数据库模糊查询", e);
            return 0L;
        }
    }

    /** 按主键游标分批把库中动态全量写入索引 */
    private long reindexAll() {
        long total = 0L;
        long lastId = 0L;
        while (true) {
            List<Space> batch = spaceMapper.selectList(new LambdaQueryWrapper<Space>()
                    .gt(Space::getId, lastId)
                    .orderByAsc(Space::getId)
                    .last("limit " + REINDEX_BATCH_SIZE));
            if (batch.isEmpty()) {
                break;
            }
            elasticsearchOperations.save(batch.stream().map(this::toDoc).toList());
            lastId = batch.get(batch.size() - 1).getId();
            total += batch.size();
        }
        return total;
    }

    @Override
    public PageResult<Long> searchIds(SpacePageQueryDTO query, String keyword,
                                      List<Long> friendIds, Long callerId) {
        if (!isAvailable() || !StringUtils.hasText(keyword)) {
            return null;
        }
        try {
            NativeQuery nativeQuery = NativeQuery.builder()
                    .withQuery(q -> q.bool(buildQuery(query, keyword, friendIds, callerId)))
                    .withSort(buildSort(query))
                    .withPageable(PageRequest.of((int) (query.getPageNum() - 1), (int) query.getPageSize()))
                    .withTrackTotalHits(true)
                    .build();
            SearchHits<SpaceDoc> hits = elasticsearchOperations.search(nativeQuery, SpaceDoc.class);
            List<Long> ids = new ArrayList<>();
            for (SearchHit<SpaceDoc> hit : hits.getSearchHits()) {
                ids.add(Long.valueOf(hit.getContent().getId()));
            }
            return PageResult.of(hits.getTotalHits(), query.getPageNum(), query.getPageSize(), ids);
        } catch (Exception e) {
            markDegraded("动态全文检索失败，降级为数据库模糊查询，keyword=" + keyword, e);
            return null;
        }
    }

    /**
     * 组装检索条件：关键词命中标题或正文即可（短语完整命中时额外加权），
     * 分类、宠物、作者、时间与可见性均以 filter 形式参与，不影响相关度打分。
     */
    private BoolQuery buildQuery(SpacePageQueryDTO query, String keyword,
                                 List<Long> friendIds, Long callerId) {
        BoolQuery.Builder bool = new BoolQuery.Builder();
        // fuzziness 让用户只记得大概关键词（含错别字）时仍能召回，minimum_should_match 控制过度宽松
        bool.must(m -> m.bool(inner -> inner
                .should(s -> s.multiMatch(mm -> mm
                        .query(keyword)
                        .fields("title^3", "content")
                        .type(TextQueryType.BestFields)
                        .fuzziness("AUTO")
                        .minimumShouldMatch("30%")))
                .should(s -> s.matchPhrase(mp -> mp.field("title").query(keyword).boost(4.0f)))
                .should(s -> s.matchPhrase(mp -> mp.field("content").query(keyword).boost(2.0f)))
                .minimumShouldMatch("1")));

        if (StringUtils.hasText(query.getCategory())) {
            bool.filter(f -> f.term(t -> t.field("category").value(query.getCategory())));
        }
        if (query.getPetId() != null) {
            bool.filter(f -> f.term(t -> t.field("petId").value(query.getPetId())));
        }
        if (query.getUserId() != null) {
            bool.filter(f -> f.term(t -> t.field("userId").value(query.getUserId())));
        }
        if (query.getStartTime() != null || query.getEndTime() != null) {
            bool.filter(f -> f.range(r -> r.untyped(u -> {
                u.field("createTime");
                if (query.getStartTime() != null) {
                    u.gte(JsonData.of(toEpochMilli(query.getStartTime())));
                }
                if (query.getEndTime() != null) {
                    u.lte(JsonData.of(toEpochMilli(query.getEndTime())));
                }
                return u;
            })));
        }
        bool.filter(f -> f.bool(visible -> {
            // 与数据库侧一致：公开动态、本人动态可见；仅好友动态需作者是当前用户的好友
            visible.should(s -> s.term(t -> t.field("visibility").value(SpaceVisibility.PUBLIC.getCode())));
            if (callerId != null) {
                visible.should(s -> s.term(t -> t.field("userId").value(callerId)));
                if (friendIds != null && !friendIds.isEmpty()) {
                    List<FieldValue> friendValues = friendIds.stream().map(FieldValue::of).toList();
                    visible.should(s -> s.bool(friendOnly -> friendOnly
                            .must(m -> m.term(t -> t.field("visibility")
                                    .value(SpaceVisibility.FRIENDS_ONLY.getCode())))
                            .must(m -> m.terms(ts -> ts.field("userId")
                                    .terms(tv -> tv.value(friendValues))))));
                }
            }
            return visible.minimumShouldMatch("1");
        }));
        return bool.build();
    }

    /** 热度排序以点赞数为主、相关度兜底；其余情况相关度优先，再按发布时间倒序 */
    private List<SortOptions> buildSort(SpacePageQueryDTO query) {
        SortOptions byScore = SortOptions.of(s -> s.score(sc -> sc.order(SortOrder.Desc)));
        if ("hot".equalsIgnoreCase(query.getSort())) {
            return List.of(
                    SortOptions.of(s -> s.field(f -> f.field("likeCount").order(SortOrder.Desc))),
                    byScore);
        }
        return List.of(
                byScore,
                SortOptions.of(s -> s.field(f -> f.field("createTime").order(SortOrder.Desc))));
    }

    /** DO 转 ES 文档 */
    private SpaceDoc toDoc(Space space) {
        SpaceDoc doc = new SpaceDoc();
        doc.setId(String.valueOf(space.getId()));
        doc.setTitle(space.getTitle());
        doc.setContent(space.getContent());
        doc.setCategory(space.getCategory());
        doc.setPetId(space.getPetId());
        doc.setUserId(space.getUserId());
        doc.setVisibility(space.getVisibility());
        doc.setLikeCount(space.getLikeCount() == null ? 0 : space.getLikeCount());
        doc.setCreateTime(space.getCreateTime() == null
                ? System.currentTimeMillis() : toEpochMilli(space.getCreateTime()));
        return doc;
    }

    private long toEpochMilli(LocalDateTime time) {
        return time.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
    }

    /** 记录异常并进入降级窗口 */
    private void markDegraded(String message, Exception e) {
        degradedUntil = System.currentTimeMillis() + DEGRADE_WINDOW_MILLIS;
        log.warn("{}（后续{}秒内不再访问 Elasticsearch）", message, DEGRADE_WINDOW_MILLIS / 1000, e);
    }
}
