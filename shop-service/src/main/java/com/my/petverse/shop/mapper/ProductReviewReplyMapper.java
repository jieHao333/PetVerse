package com.my.petverse.shop.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.shop.ProductReviewReply;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.Collection;

/**
 * 商品评价回复数据访问接口
 */
@Mapper
public interface ProductReviewReplyMapper extends BaseMapper<ProductReviewReply> {

    /** 物理删除指定评价下的全部回复（商家撤销冷静期结束后级联清理） */
    @Delete("<script>DELETE FROM product_review_reply WHERE review_id IN "
            + "<foreach collection='reviewIds' item='reviewId' open='(' separator=',' close=')'>"
            + "#{reviewId}</foreach></script>")
    int physicalDeleteByReviewIds(@Param("reviewIds") Collection<Long> reviewIds);
}
