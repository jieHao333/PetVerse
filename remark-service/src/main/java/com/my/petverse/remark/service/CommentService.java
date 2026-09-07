package com.my.petverse.remark.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.remark.CommentPageQueryDTO;
import com.my.petverse.common.dto.remark.CommentSaveDTO;
import com.my.petverse.common.entity.remark.Comment;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.remark.CommentVO;

import java.util.List;
import java.util.Map;

/**
 * 通用评论服务接口（所有登录用户均可评论，与点赞同构支持多场景扩展）
 */
public interface CommentService extends IService<Comment> {

    /**
     * 发表评论（支持回复某条评论，平铺展示）
     *
     * @param userId 评论人用户ID
     * @param dto    评论参数
     * @return 评论信息（含评论人与被回复人昵称头像）
     */
    CommentVO saveComment(Long userId, CommentSaveDTO dto);

    /**
     * 分页查询指定对象的评论（按时间正序展示互动过程）
     *
     * @param dto 分页查询参数
     * @return 评论分页结果
     */
    PageResult<CommentVO> pageComments(CommentPageQueryDTO dto);

    /**
     * 删除评论（仅评论人本人可删除，逻辑删除）
     *
     * @param userId    评论人用户ID
     * @param commentId 评论ID
     * @return 是否成功
     */
    boolean deleteComment(Long userId, Long commentId);

    /**
     * 批量统计评论数（列表页展示每条动态的评论数，一条 group by SQL 完成）
     *
     * @param targetType 评论对象类型
     * @param targetIds  对象ID列表
     * @return 对象ID → 评论数
     */
    Map<Long, Long> batchCounts(Integer targetType, List<Long> targetIds);
}
