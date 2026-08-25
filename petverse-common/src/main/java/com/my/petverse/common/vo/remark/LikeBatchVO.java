package com.my.petverse.common.vo.remark;

import lombok.Data;

import java.io.Serializable;
import java.util.Map;
import java.util.Set;

/**
 * 点赞批量查询结果：各对象点赞数 + 当前用户已点赞的对象集合
 */
@Data
public class LikeBatchVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** targetId -> 点赞数 */
    private Map<Long, Long> counts;

    /** 当前用户已点赞的 targetId 集合 */
    private Set<Long> likedIds;
}
