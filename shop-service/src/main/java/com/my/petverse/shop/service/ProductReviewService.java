package com.my.petverse.shop.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.dto.shop.ProductReviewPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductReviewReplyPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductReviewReplySaveDTO;
import com.my.petverse.common.dto.shop.ProductReviewSaveDTO;
import com.my.petverse.common.dto.shop.ProductReviewUpdateDTO;
import com.my.petverse.common.entity.shop.ProductReview;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.shop.MyReviewVO;
import com.my.petverse.common.vo.shop.ProductReviewReplyVO;
import com.my.petverse.common.vo.shop.ProductReviewSummaryVO;
import com.my.petverse.common.vo.shop.ProductReviewVO;

/**
 * 商品评价服务接口（仅已完成订单的买家可评价）
 */
public interface ProductReviewService extends IService<ProductReview> {

    /**
     * 发表商品评价：校验当前用户存在包含该商品的已完成订单且未评价过
     *
     * @param userId 买家用户ID
     * @param dto    评价参数
     * @return 评价信息
     */
    ProductReviewVO saveReview(Long userId, ProductReviewSaveDTO dto);

    /**
     * 修改商品评价（仅评价人本人可修改，评分与内容整体覆盖）
     *
     * @param userId 评价人用户ID
     * @param dto    修改参数
     * @return 修改后的评价信息
     */
    ProductReviewVO updateReview(Long userId, ProductReviewUpdateDTO dto);

    /**
     * 删除商品评价（仅评价人本人可删除，逻辑删除；删除后无法重新评价）
     *
     * @param userId   评价人用户ID
     * @param reviewId 评价ID
     * @return 是否成功
     */
    boolean deleteReview(Long userId, Long reviewId);

    /**
     * 分页查询商品评价（支持按星级筛选，聚合评价人昵称与头像）
     *
     * @param dto 分页查询参数
     * @return 评价分页结果
     */
    PageResult<ProductReviewVO> pageReviews(ProductReviewPageQueryDTO dto);

    /**
     * 分页查询我的评价（按评价时间倒序，聚合商品信息、店铺名称与回复互动数）
     *
     * @param userId 当前登录用户ID
     * @param dto    分页查询参数
     * @return 我的评价分页结果
     */
    PageResult<MyReviewVO> pageMyReviews(Long userId, BasePageQuery dto);

    /**
     * 查询商品评价汇总（平均分、总数与当前用户评价资格）
     *
     * @param productId 商品ID
     * @param userId    当前登录用户ID（可为空，为空时不返回评价资格）
     * @return 评价汇总信息
     */
    ProductReviewSummaryVO getSummary(Long productId, Long userId);

    /**
     * 发表评价回复（所有登录用户可在评价下自由互动，支持回复某条回复）
     *
     * @param userId 回复人用户ID
     * @param dto    回复参数
     * @return 回复信息
     */
    ProductReviewReplyVO saveReply(Long userId, ProductReviewReplySaveDTO dto);

    /**
     * 分页查询评价回复（按时间正序，聚合回复人与被回复人昵称头像）
     *
     * @param dto 分页查询参数
     * @return 回复分页结果
     */
    PageResult<ProductReviewReplyVO> pageReplies(ProductReviewReplyPageQueryDTO dto);

    /**
     * 删除评价回复（仅回复人本人可删除）
     *
     * @param userId  回复人用户ID
     * @param replyId 回复ID
     * @return 是否成功
     */
    boolean deleteReply(Long userId, Long replyId);
}
