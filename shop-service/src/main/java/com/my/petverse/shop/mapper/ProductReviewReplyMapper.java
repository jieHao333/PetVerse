package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.ProductReviewReply;
import org.apache.ibatis.annotations.Mapper;

/**
 * 商品评价回复数据访问接口
 */
@Mapper
public interface ProductReviewReplyMapper extends BaseMapper<ProductReviewReply> {
}
