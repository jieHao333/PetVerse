package com.my.petverse.data.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.my.petverse.common.entity.data.DataModel;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.data.ModelVO;
import com.my.petverse.data.mapper.DataModelMapper;
import com.my.petverse.data.service.DataModelService;
import com.my.petverse.data.support.AggValues;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.stream.Collectors;

/**
 * 模型服务实现：轻量逻辑回归（纯 Java 批量梯度下降，无第三方 ML 依赖，契合小服务器）。
 * 特征口径在训练（此处）与服务（ai-service 打分）两端严格一致，避免训练-服务偏斜：
 * x = [1(偏置), ctr, min(hotScore,100)/100, log1p(exposureCnt)/10]，label = 是否点击。
 * 留出 20% 样本评估 AUC，按版本注册并激活，旧激活版本自动停用。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DataModelServiceImpl extends ServiceImpl<DataModelMapper, DataModel>
        implements DataModelService {

    /** 模型键：推荐重排点击率模型 */
    private static final String MODEL_KEY = "rerank_ctr";
    private static final String ALGORITHM = "logreg";
    /** 训练所需最少曝光样本数 */
    private static final int MIN_SAMPLES = 30;
    /** 特征名（与 ai-service 打分口径一致） */
    private static final List<String> FEATURE_NAMES = List.of("bias", "ctr", "hot_score", "log_exposure");

    private final ObjectMapper objectMapper;

    @Override
    public ModelVO train() {
        List<Map<String, Object>> rows = baseMapper.selectTrainingRows();
        if (rows.size() < MIN_SAMPLES) {
            throw new BusinessException(ResultCode.BAD_REQUEST,
                    "反馈样本不足（需≥" + MIN_SAMPLES + "条商品曝光样本），请先积累行为数据再训练");
        }
        int n = rows.size();
        double[][] x = new double[n][];
        int[] y = new int[n];
        for (int i = 0; i < n; i++) {
            Map<String, Object> row = rows.get(i);
            x[i] = features(row);
            y[i] = (int) AggValues.getLong(row, "label");
        }

        // 确定性切分：固定种子打乱后取 20% 作留出评估集，保证结果可复现
        List<Integer> order = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            order.add(i);
        }
        Collections.shuffle(order, new Random(42));
        int testN = Math.max(1, n / 5);
        int[] testIdx = new int[testN];
        int[] trainIdx = new int[n - testN];
        for (int i = 0; i < n; i++) {
            if (i < testN) {
                testIdx[i] = order.get(i);
            } else {
                trainIdx[i - testN] = order.get(i);
            }
        }

        double[] w = trainLogReg(x, y, trainIdx);
        double auc = auc(x, y, testIdx, w);

        int nextVersion = maxVersion() + 1;
        // 同一模型键仅保留一个激活版本：先停用旧激活，再写入新激活
        update(new LambdaUpdateWrapper<DataModel>()
                .eq(DataModel::getModelKey, MODEL_KEY)
                .eq(DataModel::getStatus, 1)
                .set(DataModel::getStatus, 0));
        DataModel model = new DataModel();
        model.setModelKey(MODEL_KEY);
        model.setVersion(nextVersion);
        model.setAlgorithm(ALGORITHM);
        model.setFeatures(toJson(FEATURE_NAMES));
        model.setWeights(toJson(w));
        model.setAuc(round(auc, 4));
        model.setSampleCount(n);
        model.setStatus(1);
        model.setRemark("微批自动训练");
        save(model);
        log.info("重排模型训练完成：version={}, samples={}, auc={}", nextVersion, n, round(auc, 4));
        return toVO(model);
    }

    @Override
    public ModelVO getActiveModel() {
        List<DataModel> list = baseMapper.selectList(new LambdaQueryWrapper<DataModel>()
                .eq(DataModel::getModelKey, MODEL_KEY)
                .eq(DataModel::getStatus, 1)
                .orderByDesc(DataModel::getVersion));
        return list.isEmpty() ? null : toVO(list.get(0));
    }

    @Override
    public List<ModelVO> listModels() {
        List<DataModel> list = baseMapper.selectList(new LambdaQueryWrapper<DataModel>()
                .orderByDesc(DataModel::getModelKey)
                .orderByDesc(DataModel::getVersion));
        return list.stream().map(this::toVO).collect(Collectors.toList());
    }

    /** 特征变换：训练与服务两端必须完全一致 */
    private double[] features(Map<String, Object> row) {
        double ctr = AggValues.getDouble(row, "ctr");
        double hot = AggValues.getDouble(row, "hotScore");
        double exposure = AggValues.getDouble(row, "exposureCnt");
        return new double[]{
                1.0,
                clamp(ctr, 0, 1),
                clamp(hot, 0, 100) / 100.0,
                Math.log1p(Math.max(exposure, 0)) / 10.0
        };
    }

    /** 批量梯度下降训练逻辑回归（含 L2 正则） */
    private double[] trainLogReg(double[][] x, int[] y, int[] trainIdx) {
        int d = FEATURE_NAMES.size();
        double[] w = new double[d];
        double lr = 0.5;
        double l2 = 0.01;
        int iterations = 400;
        int n = trainIdx.length;
        for (int it = 0; it < iterations; it++) {
            double[] grad = new double[d];
            for (int idx : trainIdx) {
                double err = sigmoid(dot(w, x[idx])) - y[idx];
                for (int j = 0; j < d; j++) {
                    grad[j] += err * x[idx][j];
                }
            }
            for (int j = 0; j < d; j++) {
                w[j] -= lr * (grad[j] / n + l2 * w[j]);
            }
        }
        return w;
    }

    /** 留出集 AUC（Mann-Whitney U，含并列秩处理） */
    private double auc(double[][] x, int[] y, int[] testIdx, double[] w) {
        int n = testIdx.length;
        double[] scores = new double[n];
        int[] labels = new int[n];
        for (int i = 0; i < n; i++) {
            scores[i] = sigmoid(dot(w, x[testIdx[i]]));
            labels[i] = y[testIdx[i]];
        }
        Integer[] order = new Integer[n];
        for (int i = 0; i < n; i++) {
            order[i] = i;
        }
        Arrays.sort(order, (a, b) -> Double.compare(scores[a], scores[b]));
        double[] ranks = new double[n];
        int i = 0;
        while (i < n) {
            int j = i;
            while (j + 1 < n && scores[order[j + 1]] == scores[order[i]]) {
                j++;
            }
            double avgRank = (i + 1 + j + 1) / 2.0;
            for (int k = i; k <= j; k++) {
                ranks[order[k]] = avgRank;
            }
            i = j + 1;
        }
        double sumPos = 0;
        int nPos = 0;
        int nNeg = 0;
        for (int k = 0; k < n; k++) {
            if (labels[k] == 1) {
                sumPos += ranks[k];
                nPos++;
            } else {
                nNeg++;
            }
        }
        if (nPos == 0 || nNeg == 0) {
            return 0.5;
        }
        return (sumPos - nPos * (nPos + 1) / 2.0) / ((double) nPos * nNeg);
    }

    private int maxVersion() {
        List<DataModel> list = baseMapper.selectList(new LambdaQueryWrapper<DataModel>()
                .eq(DataModel::getModelKey, MODEL_KEY));
        return list.stream().mapToInt(m -> m.getVersion() == null ? 0 : m.getVersion()).max().orElse(0);
    }

    private ModelVO toVO(DataModel m) {
        ModelVO vo = new ModelVO();
        vo.setModelKey(m.getModelKey());
        vo.setVersion(m.getVersion());
        vo.setAlgorithm(m.getAlgorithm());
        vo.setFeatures(parseList(m.getFeatures(), new TypeReference<List<String>>() {
        }));
        vo.setWeights(parseList(m.getWeights(), new TypeReference<List<Double>>() {
        }));
        vo.setAuc(m.getAuc());
        vo.setSampleCount(m.getSampleCount());
        vo.setStatus(m.getStatus());
        vo.setRemark(m.getRemark());
        vo.setTrainedAt(m.getCreateTime());
        return vo;
    }

    private double sigmoid(double z) {
        if (z >= 0) {
            return 1.0 / (1.0 + Math.exp(-z));
        }
        double e = Math.exp(z);
        return e / (1.0 + e);
    }

    private double dot(double[] w, double[] x) {
        double s = 0;
        for (int i = 0; i < w.length; i++) {
            s += w[i] * x[i];
        }
        return s;
    }

    private double clamp(double v, double lo, double hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    private double round(double v, int scale) {
        double factor = Math.pow(10, scale);
        return Math.round(v * factor) / factor;
    }

    private String toJson(Object obj) {
        try {
            return objectMapper.writeValueAsString(obj);
        } catch (Exception e) {
            return "[]";
        }
    }

    private <T> T parseList(String json, TypeReference<T> type) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return objectMapper.readValue(json, type);
        } catch (Exception e) {
            return null;
        }
    }
}