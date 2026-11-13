package com.my.petverse.common.dto.data;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;
import java.util.List;

/**
 * 行为埋点批量上报入参。单次最多 50 条，避免超大请求体。
 */
@Data
public class TrackBatchDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 事件列表 */
    @NotEmpty(message = "事件列表不能为空")
    @Size(max = 50, message = "单次最多上报50条事件")
    @Valid
    private List<TrackEventDTO> events;
}