package com.my.petverse.common.dto.data;

import com.my.petverse.common.dto.base.BasePageQuery;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 原始事件分页查询入参（管理端 BI 排查用）。
 */
@Data
@EqualsAndHashCode(callSuper = true)
public class DataEventQueryDTO extends BasePageQuery {

    private static final long serialVersionUID = 1L;

    /** 事件来源：behavior/business */
    private String source;
    /** 事件类型 */
    private String eventType;
    /** 用户ID */
    private Long userId;
    /** 场景 */
    private String scene;
}