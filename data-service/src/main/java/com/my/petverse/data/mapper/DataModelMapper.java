package com.my.petverse.data.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.data.DataModel;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

/**
 * 模型注册表 Mapper，附带训练样本查询（反馈样本 ⋈ 商品统计特征）。
 */
@Mapper
public interface DataModelMapper extends BaseMapper<DataModel> {

    /** 取商品曝光样本及其统计特征与点击标签，供重排模型训练 */
    @Select("SELECT p.ctr AS ctr, p.hot_score AS hotScore, p.exposure_cnt AS exposureCnt, s.clicked AS label "
            + "FROM data_sample s JOIN data_product_stats p ON s.target_id = p.product_id "
            + "WHERE s.target_type = 'product' AND s.exposed = 1 AND s.deleted = 0 AND p.deleted = 0")
    List<Map<String, Object>> selectTrainingRows();
}