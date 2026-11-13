package com.my.petverse.data.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.entity.data.DataProductStats;
import com.my.petverse.common.entity.data.DataSample;
import com.my.petverse.common.entity.data.DataUserProfile;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.data.ProductStatsVO;
import com.my.petverse.common.vo.data.UserProfileVO;
import com.my.petverse.data.mapper.DataBehaviorMapper;
import com.my.petverse.data.mapper.DataProductStatsMapper;
import com.my.petverse.data.mapper.DataSampleMapper;
import com.my.petverse.data.mapper.DataUserProfileMapper;
import com.my.petverse.data.service.DataAssetService;
import com.my.petverse.data.support.AggValues;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 数据资产服务实现。
 * 聚合全部由行为明细（DWD）驱动，自包含、不跨服务现拉；按唯一键 upsert，可重复执行（幂等）。
 * 画像/特征以 JSON 存储物种亲和度与标签，Data API 读取时还原为结构化 VO。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DataAssetServiceImpl extends ServiceImpl<DataUserProfileMapper, DataUserProfile>
        implements DataAssetService {

    /** 特征版本号：口径调整时递增，便于缓存失效与灰度 */
    private static final int FEATURE_VERSION = 1;

    private final DataProductStatsMapper productStatsMapper;
    private final DataSampleMapper sampleMapper;
    private final DataBehaviorMapper behaviorMapper;
    private final ObjectMapper objectMapper;

    @Override
    public void aggregate() {
        aggregateProductStats();
        aggregateUserProfiles();
        aggregateSamples(LocalDate.now());
    }

    /** 商品统计：曝光/点击/CTR/热度 */
    private void aggregateProductStats() {
        List<Map<String, Object>> rows = behaviorMapper.aggregateProductStats();
        for (Map<String, Object> row : rows) {
            long productId = AggValues.getLong(row, "productId");
            if (productId <= 0) {
                continue;
            }
            long exposure = AggValues.getLong(row, "exposureCnt");
            long click = AggValues.getLong(row, "clickCnt");
            long order = AggValues.getLong(row, "orderCnt");
            long review = AggValues.getLong(row, "reviewCnt");
            double ctr = exposure > 0 ? (double) click / exposure : 0d;
            double hot = click * 2.0 + exposure * 0.1 + ctr * 10.0 + order * 3.0;
            DataProductStats stats = productStatsMapper.selectOne(new LambdaQueryWrapper<DataProductStats>()
                    .eq(DataProductStats::getProductId, productId));
            if (stats == null) {
                stats = new DataProductStats();
                stats.setProductId(productId);
            }
            stats.setExposureCnt(exposure);
            stats.setClickCnt(click);
            stats.setCtr(round(ctr, 4));
            stats.setOrderCnt(order);
            stats.setReviewCnt(review);
            stats.setHotScore(round(hot, 2));
            if (stats.getId() == null) {
                productStatsMapper.insert(stats);
            } else {
                productStatsMapper.updateById(stats);
            }
        }
    }

    /** 用户画像：行为计数 + 物种亲和度 + 活跃度/消费力分级 + 标签 */
    private void aggregateUserProfiles() {
        List<Map<String, Object>> rows = behaviorMapper.aggregateUserBehavior();
        List<Map<String, Object>> affinityRows = behaviorMapper.aggregateSpeciesAffinity();
        Map<Long, Map<String, Double>> affinityByUser = new LinkedHashMap<>();
        for (Map<String, Object> row : affinityRows) {
            long uid = AggValues.getLong(row, "userId");
            Object speciesVal = row.get("species");
            if (uid <= 0 || speciesVal == null) {
                continue;
            }
            String species = speciesVal.toString();
            double weight = AggValues.getDouble(row, "weight");
            affinityByUser.computeIfAbsent(uid, k -> new LinkedHashMap<>())
                    .merge(species, weight, Double::sum);
        }
        for (Map<String, Object> row : rows) {
            long uid = AggValues.getLong(row, "userId");
            if (uid <= 0) {
                continue;
            }
            long exposure = AggValues.getLong(row, "exposureCnt");
            long click = AggValues.getLong(row, "clickCnt");
            long search = AggValues.getLong(row, "searchCnt");
            long order = AggValues.getLong(row, "orderCnt");
            long aiUp = AggValues.getLong(row, "aiUp");
            long aiDown = AggValues.getLong(row, "aiDown");
            long total = AggValues.getLong(row, "totalCnt");
            LocalDateTime lastActive = AggValues.asDateTime(row.get("lastActive"));
            double ctr = exposure > 0 ? (double) click / exposure : 0d;

            Map<String, Double> affinity = affinityByUser.getOrDefault(uid, new LinkedHashMap<>());
            List<String> speciesSorted = affinity.entrySet().stream()
                    .sorted((a, b) -> Double.compare(b.getValue(), a.getValue()))
                    .map(Map.Entry::getKey)
                    .collect(Collectors.toList());
            String activity = total >= 50 ? "high" : total >= 10 ? "medium" : "low";
            String consumption = order >= 3 ? "high" : (order >= 1 || click >= 10) ? "medium" : "low";
            List<String> tags = new ArrayList<>();
            if ("high".equals(activity)) {
                tags.add("高活跃");
            }
            if (aiUp > 0) {
                tags.add("AI活跃");
            }
            for (String s : speciesSorted) {
                if (tags.size() >= 6) {
                    break;
                }
                tags.add("养" + s);
            }

            DataUserProfile profile = baseMapper.selectOne(new LambdaQueryWrapper<DataUserProfile>()
                    .eq(DataUserProfile::getUserId, uid));
            if (profile == null) {
                profile = new DataUserProfile();
                profile.setUserId(uid);
            }
            profile.setOwnSpecies(String.join(",", speciesSorted));
            profile.setSpeciesAffinity(toJson(affinity));
            profile.setActivityLevel(activity);
            profile.setConsumptionLevel(consumption);
            profile.setExposureCnt(exposure);
            profile.setClickCnt(click);
            profile.setCtr(round(ctr, 4));
            profile.setSearchCnt(search);
            profile.setAiFeedbackUp(aiUp);
            profile.setAiFeedbackDown(aiDown);
            profile.setLastActiveTime(lastActive);
            profile.setTags(toJson(tags));
            profile.setFeatureVersion(FEATURE_VERSION);
            if (profile.getId() == null) {
                save(profile);
            } else {
                updateById(profile);
            }
        }
    }

    /** 反馈样本：当日曝光⋈点击，label=是否点击 */
    private void aggregateSamples(LocalDate date) {
        List<Map<String, Object>> rows = behaviorMapper.aggregateSamples(date);
        for (Map<String, Object> row : rows) {
            long uid = AggValues.getLong(row, "userId");
            long targetId = AggValues.getLong(row, "targetId");
            if (uid <= 0 || targetId <= 0) {
                continue;
            }
            String scene = strOrEmpty(row.get("scene"));
            String targetType = strOrEmpty(row.get("targetType"));
            int exposed = (int) AggValues.getLong(row, "exposed");
            int clicked = (int) AggValues.getLong(row, "clicked");
            Integer position = row.get("position") == null ? null : (int) AggValues.getLong(row, "position");
            LocalDateTime eventTime = AggValues.asDateTime(row.get("eventTime"));
            DataSample sample = sampleMapper.selectOne(new LambdaQueryWrapper<DataSample>()
                    .eq(DataSample::getUserId, uid)
                    .eq(DataSample::getScene, scene)
                    .eq(DataSample::getTargetType, targetType)
                    .eq(DataSample::getTargetId, targetId)
                    .eq(DataSample::getSampleDate, date));
            if (sample == null) {
                sample = new DataSample();
                sample.setUserId(uid);
                sample.setScene(scene);
                sample.setTargetType(targetType);
                sample.setTargetId(targetId);
                sample.setSampleDate(date);
            }
            sample.setPosition(position);
            sample.setExposed(exposed);
            sample.setClicked(clicked);
            sample.setFeedback(0);
            sample.setLabel(clicked);
            sample.setEventTime(eventTime);
            if (sample.getId() == null) {
                sampleMapper.insert(sample);
            } else {
                sampleMapper.updateById(sample);
            }
        }
    }

    @Override
    public UserProfileVO getProfile(Long userId) {
        DataUserProfile profile = baseMapper.selectOne(new LambdaQueryWrapper<DataUserProfile>()
                .eq(DataUserProfile::getUserId, userId));
        return toProfileVO(profile, userId);
    }

    @Override
    public List<ProductStatsVO> listProductStats(List<Long> productIds) {
        if (productIds == null || productIds.isEmpty()) {
            return List.of();
        }
        List<DataProductStats> list = productStatsMapper.selectList(new LambdaQueryWrapper<DataProductStats>()
                .in(DataProductStats::getProductId, productIds));
        return list.stream().map(this::toStatsVO).collect(Collectors.toList());
    }

    @Override
    public PageResult<UserProfileVO> pageProfiles(BasePageQuery query) {
        Page<DataUserProfile> page = baseMapper.selectPage(new Page<>(query.getPageNum(), query.getPageSize()),
                new LambdaQueryWrapper<DataUserProfile>().orderByDesc(DataUserProfile::getUpdateTime));
        List<UserProfileVO> vos = page.getRecords().stream()
                .map(p -> toProfileVO(p, p.getUserId())).collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    public PageResult<ProductStatsVO> pageProductStats(BasePageQuery query) {
        Page<DataProductStats> page = productStatsMapper.selectPage(new Page<>(query.getPageNum(), query.getPageSize()),
                new LambdaQueryWrapper<DataProductStats>().orderByDesc(DataProductStats::getHotScore));
        List<ProductStatsVO> vos = page.getRecords().stream().map(this::toStatsVO).collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    private UserProfileVO toProfileVO(DataUserProfile p, Long userId) {
        UserProfileVO vo = new UserProfileVO();
        vo.setUserId(userId);
        vo.setFeatureVersion(FEATURE_VERSION);
        if (p == null) {
            vo.setSpeciesAffinity(new LinkedHashMap<>());
            vo.setTags(new ArrayList<>());
            vo.setExposureCnt(0L);
            vo.setClickCnt(0L);
            vo.setCtr(0d);
            vo.setSearchCnt(0L);
            vo.setAiFeedbackUp(0L);
            vo.setAiFeedbackDown(0L);
            vo.setActivityLevel("low");
            vo.setConsumptionLevel("low");
            return vo;
        }
        vo.setOwnSpecies(p.getOwnSpecies());
        vo.setSpeciesAffinity(parseMap(p.getSpeciesAffinity()));
        vo.setActivityLevel(p.getActivityLevel());
        vo.setConsumptionLevel(p.getConsumptionLevel());
        vo.setExposureCnt(p.getExposureCnt());
        vo.setClickCnt(p.getClickCnt());
        vo.setCtr(p.getCtr());
        vo.setSearchCnt(p.getSearchCnt());
        vo.setAiFeedbackUp(p.getAiFeedbackUp());
        vo.setAiFeedbackDown(p.getAiFeedbackDown());
        vo.setTags(parseList(p.getTags()));
        vo.setLastActiveTime(p.getLastActiveTime());
        vo.setFeatureVersion(p.getFeatureVersion());
        return vo;
    }

    private ProductStatsVO toStatsVO(DataProductStats s) {
        ProductStatsVO vo = new ProductStatsVO();
        vo.setProductId(s.getProductId());
        vo.setExposureCnt(s.getExposureCnt());
        vo.setClickCnt(s.getClickCnt());
        vo.setCtr(s.getCtr());
        vo.setOrderCnt(s.getOrderCnt());
        vo.setReviewCnt(s.getReviewCnt());
        vo.setHotScore(s.getHotScore());
        return vo;
    }

    private String toJson(Object obj) {
        try {
            return objectMapper.writeValueAsString(obj);
        } catch (Exception e) {
            return "{}";
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> parseMap(String json) {
        if (json == null || json.isBlank()) {
            return new LinkedHashMap<>();
        }
        try {
            return objectMapper.readValue(json, Map.class);
        } catch (Exception e) {
            return new LinkedHashMap<>();
        }
    }

    @SuppressWarnings("unchecked")
    private List<String> parseList(String json) {
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        try {
            return objectMapper.readValue(json, List.class);
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }

    private String strOrEmpty(Object v) {
        return v == null ? "" : v.toString();
    }

    private double round(double v, int scale) {
        double factor = Math.pow(10, scale);
        return Math.round(v * factor) / factor;
    }
}