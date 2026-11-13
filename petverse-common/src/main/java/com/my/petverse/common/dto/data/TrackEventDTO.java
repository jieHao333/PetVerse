package com.my.petverse.common.dto.data;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 单条行为埋点事件入参。前端 SDK 批量上报，服务端按 eventId 幂等落地。
 */
@Data
public class TrackEventDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 客户端事件ID（幂等键，缺省由服务端生成） */
    private String eventId;
    /** 事件类型：exposure/click/search/ai_feedback/page_view */
    @NotBlank(message = "事件类型不能为空")
    @Size(max = 32, message = "事件类型过长")
    private String eventType;
    /** 目标对象类型：product/space/ai_message/page */
    @Size(max = 32, message = "目标类型过长")
    private String targetType;
    /** 目标对象ID */
    private Long targetId;
    /** 场景：home/shop/pet_chat 等 */
    @Size(max = 32, message = "场景过长")
    private String scene;
    /** 曝光/点击位置（推荐流序号） */
    private Integer position;
    /** 搜索关键词 */
    @Size(max = 128, message = "关键词过长")
    private String keyword;
    /** 关联物种 */
    @Size(max = 32, message = "物种过长")
    private String species;
    /** AI反馈：1-有用 -1-无用 0-无 */
    private Integer feedback;
    /** 宠物ID（可空） */
    private Long petId;
    /** 事件时间（epoch 毫秒，缺省取服务端当前时间） */
    private Long eventTime;
}