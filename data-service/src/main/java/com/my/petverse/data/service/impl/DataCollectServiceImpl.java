package com.my.petverse.data.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.dto.data.DataEventQueryDTO;
import com.my.petverse.common.dto.data.TrackEventDTO;
import com.my.petverse.common.entity.data.DataBehavior;
import com.my.petverse.common.entity.data.DataEventRaw;
import com.my.petverse.common.mq.MqEventPublisher;
import com.my.petverse.common.mq.MqTopics;
import com.my.petverse.common.mq.message.DataEventMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.data.DataEventVO;
import com.my.petverse.data.mapper.DataBehaviorMapper;
import com.my.petverse.data.mapper.DataEventRawMapper;
import com.my.petverse.data.service.DataCollectService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 数据采集服务实现。
 * 埋点经 MQ 异步落地（复用 afterCommit + 发布确认），MQ 关闭或投递失败时本地兜底直写，保证不丢；
 * ODS/DWD 均按 eventId 幂等，业务事件以"交换机+业务键"派生 eventId，重复投递自动跳过。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DataCollectServiceImpl extends ServiceImpl<DataEventRawMapper, DataEventRaw>
        implements DataCollectService {

    private final DataBehaviorMapper behaviorMapper;
    private final MqEventPublisher mqEventPublisher;
    private final ObjectMapper objectMapper;

    @Override
    public int ingestBehavior(Long userId, List<TrackEventDTO> events) {
        if (events == null || events.isEmpty()) {
            return 0;
        }
        int accepted = 0;
        for (TrackEventDTO dto : events) {
            if (dto == null || dto.getEventType() == null || dto.getEventType().isBlank()) {
                continue;
            }
            String eventId = (dto.getEventId() != null && !dto.getEventId().isBlank())
                    ? dto.getEventId() : UUID.randomUUID().toString();
            LocalDateTime eventTime = dto.getEventTime() != null
                    ? LocalDateTime.ofInstant(Instant.ofEpochMilli(dto.getEventTime()), ZoneId.systemDefault())
                    : LocalDateTime.now();
            DataEventMessage msg = new DataEventMessage();
            msg.setEventId(eventId);
            msg.setSource("behavior");
            msg.setEventType(dto.getEventType());
            msg.setUserId(userId);
            msg.setPetId(dto.getPetId());
            msg.setTargetType(dto.getTargetType());
            msg.setTargetId(dto.getTargetId());
            msg.setScene(dto.getScene());
            msg.setPosition(dto.getPosition());
            msg.setKeyword(dto.getKeyword());
            msg.setSpecies(dto.getSpecies());
            msg.setFeedback(dto.getFeedback() == null ? 0 : dto.getFeedback());
            msg.setEventTime(eventTime.toString());
            try {
                msg.setPayload(objectMapper.writeValueAsString(dto));
            } catch (Exception e) {
                msg.setPayload("{}");
            }
            mqEventPublisher.publishAfterCommit(MqTopics.DATA_EVENT, MqTopics.TAG_DATA_BEHAVIOR, msg,
                    "data-behavior:" + eventId, () -> persistEvent(msg));
            accepted++;
        }
        return accepted;
    }

    @Override
    public void persistEvent(DataEventMessage msg) {
        if (msg == null || msg.getEventId() == null) {
            return;
        }
        String eventId = msg.getEventId();
        String source = msg.getSource() == null ? "behavior" : msg.getSource();
        LocalDateTime eventTime = parseTime(msg.getEventTime());

        Long rawCount = baseMapper.selectCount(new LambdaQueryWrapper<DataEventRaw>()
                .eq(DataEventRaw::getEventId, eventId));
        if (rawCount == null || rawCount == 0) {
            DataEventRaw raw = new DataEventRaw();
            raw.setEventId(eventId);
            raw.setSource(source);
            raw.setEventType(msg.getEventType());
            raw.setUserId(msg.getUserId());
            raw.setPetId(msg.getPetId());
            raw.setTargetType(msg.getTargetType());
            raw.setTargetId(msg.getTargetId());
            raw.setScene(msg.getScene());
            raw.setPayload(msg.getPayload());
            raw.setEventTime(eventTime);
            save(raw);
        }

        if ("behavior".equals(source)) {
            Long behaviorCount = behaviorMapper.selectCount(new LambdaQueryWrapper<DataBehavior>()
                    .eq(DataBehavior::getEventId, eventId));
            if (behaviorCount == null || behaviorCount == 0) {
                DataBehavior behavior = new DataBehavior();
                behavior.setEventId(eventId);
                behavior.setUserId(msg.getUserId());
                behavior.setEventType(msg.getEventType());
                behavior.setTargetType(msg.getTargetType());
                behavior.setTargetId(msg.getTargetId());
                behavior.setScene(msg.getScene());
                behavior.setPosition(msg.getPosition());
                behavior.setKeyword(msg.getKeyword());
                behavior.setSpecies(msg.getSpecies());
                behavior.setFeedback(msg.getFeedback() == null ? 0 : msg.getFeedback());
                behavior.setEventTime(eventTime);
                behaviorMapper.insert(behavior);
            }
        }
    }

    @Override
    public void persistBusiness(String exchange, String routingKey, String body, String key) {
        String eventId = "biz:" + exchange + ":" + (key == null ? UUID.randomUUID().toString() : key);
        Long count = baseMapper.selectCount(new LambdaQueryWrapper<DataEventRaw>()
                .eq(DataEventRaw::getEventId, eventId));
        if (count != null && count > 0) {
            return;
        }
        DataEventRaw raw = new DataEventRaw();
        raw.setEventId(eventId);
        raw.setSource("business");
        raw.setEventType(deriveEventType(exchange, routingKey));
        raw.setPayload(body);
        raw.setEventTime(LocalDateTime.now());
        try {
            Map<?, ?> map = objectMapper.readValue(body, Map.class);
            raw.setUserId(nullableLong(map.get("userId")));
            raw.setTargetId(nullableLong(map.get("targetId")));
            raw.setPetId(nullableLong(map.get("petId")));
        } catch (Exception ignored) {
            // 业务事件载荷形态各异，抽取失败不影响原始落地
        }
        save(raw);
    }

    @Override
    public PageResult<DataEventVO> pageEvents(DataEventQueryDTO query) {
        Page<DataEventRaw> page = page(new Page<>(query.getPageNum(), query.getPageSize()),
                new LambdaQueryWrapper<DataEventRaw>()
                        .eq(notBlank(query.getSource()), DataEventRaw::getSource, query.getSource())
                        .eq(notBlank(query.getEventType()), DataEventRaw::getEventType, query.getEventType())
                        .eq(query.getUserId() != null, DataEventRaw::getUserId, query.getUserId())
                        .eq(notBlank(query.getScene()), DataEventRaw::getScene, query.getScene())
                        .orderByDesc(DataEventRaw::getEventTime));
        List<DataEventVO> vos = page.getRecords().stream().map(this::toVO).collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    /** 由交换机与 RoutingKey 派生可读事件类型 */
    private String deriveEventType(String exchange, String routingKey) {
        if (MqTopics.SPACE_EVENT.equals(exchange)) {
            return "space_" + routingKey;
        }
        if (MqTopics.LIKE_CHANGED.equals(exchange)) {
            return "like_changed";
        }
        if (MqTopics.ORDER_TIMEOUT.equals(exchange)) {
            return "order_timeout";
        }
        if (MqTopics.MERCHANT_EVENT.equals(exchange)) {
            return "merchant_" + routingKey;
        }
        if (MqTopics.PROMOTION_EVENT.equals(exchange)) {
            return "coupon_receive";
        }
        return (exchange + "_" + routingKey).replaceAll("[^a-zA-Z0-9_]", "_");
    }

    private DataEventVO toVO(DataEventRaw raw) {
        DataEventVO vo = new DataEventVO();
        vo.setId(raw.getId());
        vo.setEventId(raw.getEventId());
        vo.setSource(raw.getSource());
        vo.setEventType(raw.getEventType());
        vo.setUserId(raw.getUserId());
        vo.setPetId(raw.getPetId());
        vo.setTargetType(raw.getTargetType());
        vo.setTargetId(raw.getTargetId());
        vo.setScene(raw.getScene());
        vo.setPayload(raw.getPayload());
        vo.setEventTime(raw.getEventTime());
        vo.setCreateTime(raw.getCreateTime());
        return vo;
    }

    private LocalDateTime parseTime(String s) {
        if (s == null || s.isBlank()) {
            return LocalDateTime.now();
        }
        try {
            return LocalDateTime.parse(s);
        } catch (Exception e) {
            return LocalDateTime.now();
        }
    }

    private Long nullableLong(Object v) {
        return v instanceof Number n ? n.longValue() : null;
    }

    private boolean notBlank(String s) {
        return s != null && !s.isBlank();
    }
}