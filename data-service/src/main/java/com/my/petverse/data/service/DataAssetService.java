package com.my.petverse.data.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.entity.data.DataUserProfile;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.data.ProductStatsVO;
import com.my.petverse.common.vo.data.UserProfileVO;

import java.util.List;

/**
 * 数据资产服务：微批聚合用户画像、商品统计与反馈样本，并向统一数据服务（Data API）提供读取。
 */
public interface DataAssetService extends IService<DataUserProfile> {

    /** 微批聚合：商品统计 + 用户画像 + 当日反馈样本 */
    void aggregate();

    /** 读取用户画像/特征（不存在时返回带默认值的空画像，保证 AI 侧拿到稳定结构） */
    UserProfileVO getProfile(Long userId);

    /** 批量读取商品统计（供推荐重排） */
    List<ProductStatsVO> listProductStats(List<Long> productIds);

    /** 分页查询用户画像（管理端） */
    PageResult<UserProfileVO> pageProfiles(BasePageQuery query);

    /** 分页查询商品统计（管理端，按热度降序） */
    PageResult<ProductStatsVO> pageProductStats(BasePageQuery query);
}