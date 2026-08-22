package com.my.petverse.common.dto.base;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.Data;

import java.io.Serializable;

/**
 * 分页查询基类，所有分页查询 DTO 必须继承
 */
@Data
public class BasePageQuery implements Serializable {

    /** 页码，从 1 开始 */
    @Min(value = 1, message = "页码不能小于1")
    private long pageNum = 1;

    /** 每页条数，最大 100 */
    @Min(value = 1, message = "每页条数不能小于1")
    @Max(value = 100, message = "每页条数不能大于100")
    private long pageSize = 10;
}
