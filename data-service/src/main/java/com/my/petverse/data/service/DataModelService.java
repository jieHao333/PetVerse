package com.my.petverse.data.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.entity.data.DataModel;
import com.my.petverse.common.vo.data.ModelVO;

import java.util.List;

/**
 * 模型服务：从反馈样本训练重排模型、评估、版本注册与激活，并对外提供激活模型。
 * 构成"消费→行为→样本→训练→注册→服务→更优消费"的反馈闭环。
 */
public interface DataModelService extends IService<DataModel> {

    /** 训练并注册一个新版本重排模型（样本不足时抛业务异常），返回激活模型 */
    ModelVO train();

    /** 取当前激活的重排模型（无则返回 null） */
    ModelVO getActiveModel();

    /** 模型注册表列表（管理端） */
    List<ModelVO> listModels();
}