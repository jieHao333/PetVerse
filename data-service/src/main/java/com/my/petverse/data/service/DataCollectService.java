package com.my.petverse.data.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.data.DataEventQueryDTO;
import com.my.petverse.common.dto.data.TrackEventDTO;
import com.my.petverse.common.entity.data.DataEventRaw;
import com.my.petverse.common.mq.message.DataEventMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.data.DataEventVO;

import java.util.List;

/**
 * 数据采集服务：行为埋点接入、ODS 原始事件与 DWD 行为明细落地、业务事件采集、事件查询。
 */
public interface DataCollectService extends IService<DataEventRaw> {

    /** 接入一批行为埋点，优先经 MQ 异步落地，失败本地兜底，返回受理条数 */
    int ingestBehavior(Long userId, List<TrackEventDTO> events);

    /** 幂等落地一条行为事件（ODS + DWD），供消费者与本地兜底共用 */
    void persistEvent(DataEventMessage msg);

    /** 幂等落地一条业务领域事件到 ODS（保留原始 JSON 现场） */
    void persistBusiness(String exchange, String routingKey, String body, String key);

    /** 分页查询原始事件（管理端排查） */
    PageResult<DataEventVO> pageEvents(DataEventQueryDTO query);
}