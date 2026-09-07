package com.my.petverse.shop.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.dto.shop.ProductReviewPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductReviewReplyPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductReviewReplySaveDTO;
import com.my.petverse.common.dto.shop.ProductReviewSaveDTO;
import com.my.petverse.common.dto.shop.ProductReviewUpdateDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.shop.MyReviewVO;
import com.my.petverse.common.vo.shop.ProductReviewReplyVO;
import com.my.petverse.common.vo.shop.ProductReviewSummaryVO;
import com.my.petverse.common.vo.shop.ProductReviewVO;
import com.my.petverse.shop.service.ProductReviewService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 商品评价接口控制器（买家端，所有登录用户可访问）
 * 仅已完成订单的买家可发表评价，同一用户对同一商品仅能评价一次
 */
@RestController
@RequestMapping("/shop/review")
@RequiredArgsConstructor
public class ProductReviewController {

    private final ProductReviewService productReviewService;

    /** 发表商品评价（需存在该商品的已完成订单且未评价过） */
    @PostMapping
    public Result<ProductReviewVO> save(@RequestBody @Valid ProductReviewSaveDTO dto) {
        return Result.success(productReviewService.saveReview(UserContext.getUserId(), dto));
    }

    /** 修改我的商品评价（仅评价人本人可修改） */
    @PutMapping
    public Result<ProductReviewVO> update(@RequestBody @Valid ProductReviewUpdateDTO dto) {
        return Result.success(productReviewService.updateReview(UserContext.getUserId(), dto));
    }

    /** 删除我的商品评价（仅评价人本人可删除，删除后无法重新评价） */
    @DeleteMapping("/{id}")
    public Result<Boolean> remove(@PathVariable("id") Long id) {
        return Result.success(productReviewService.deleteReview(UserContext.getUserId(), id));
    }

    /** 分页查询商品评价（支持按星级筛选，聚合评价人昵称头像） */
    @GetMapping("/page")
    public Result<PageResult<ProductReviewVO>> page(@Valid ProductReviewPageQueryDTO dto) {
        return Result.success(productReviewService.pageReviews(dto));
    }

    /** 分页查询我的评价（聚合商品信息、店铺名称与回复互动数） */
    @GetMapping("/my/page")
    public Result<PageResult<MyReviewVO>> myPage(@Valid BasePageQuery dto) {
        return Result.success(productReviewService.pageMyReviews(UserContext.getUserId(), dto));
    }

    /** 查询商品评价汇总（平均分、总数与当前用户评价资格） */
    @GetMapping("/summary/{productId}")
    public Result<ProductReviewSummaryVO> summary(@PathVariable("productId") Long productId) {
        return Result.success(productReviewService.getSummary(productId, UserContext.peekUserId()));
    }

    /** 发表评价回复（所有登录用户可在评价下自由互动，支持回复某条回复） */
    @PostMapping("/reply")
    public Result<ProductReviewReplyVO> saveReply(@RequestBody @Valid ProductReviewReplySaveDTO dto) {
        return Result.success(productReviewService.saveReply(UserContext.getUserId(), dto));
    }

    /** 分页查询评价回复（按时间正序） */
    @GetMapping("/reply/page")
    public Result<PageResult<ProductReviewReplyVO>> pageReplies(@Valid ProductReviewReplyPageQueryDTO dto) {
        return Result.success(productReviewService.pageReplies(dto));
    }

    /** 删除我的评价回复（仅回复人本人可删除） */
    @DeleteMapping("/reply/{id}")
    public Result<Boolean> removeReply(@PathVariable("id") Long id) {
        return Result.success(productReviewService.deleteReply(UserContext.getUserId(), id));
    }
}
