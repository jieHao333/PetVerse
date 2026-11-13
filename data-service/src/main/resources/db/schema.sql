-- petverse_data 数据中台数据库脚本（AI 数据中台：采集 → 建模 → 资产 → 服务）
--
-- 全新环境初始化：先创建数据库，再执行本脚本全部建表语句
-- CREATE DATABASE IF NOT EXISTS petverse_data DEFAULT CHARSET utf8mb4;
--
-- 分层说明：
--   ODS  data_event_raw    原始事件（行为埋点 + 业务领域事件，保留现场）
--   DWD  data_behavior     行为明细（清洗标准化后的统一事实表）
--   DWS  data_user_profile 用户画像/特征（微批聚合，供 Data API 一次取数）
--   DWS  data_product_stats 商品统计（曝光/点击/CTR/热度）
--   ADS  data_metric_daily 按日指标（统一口径，供 BI 看板）
--   样本 data_sample       曝光⋈点击反馈样本（供召回/重排模型训练）
-- 所有表幂等（IF NOT EXISTS），公共字段 id/create_time/update_time/deleted 与全项目一致。

CREATE DATABASE IF NOT EXISTS petverse_data DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE petverse_data;

-- ------------------------------------------------------------
-- ODS：原始事件层
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_event_raw
(
    id          BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    event_id    VARCHAR(64)  NOT NULL COMMENT '事件唯一ID(幂等键)',
    source      VARCHAR(16)  NOT NULL COMMENT '来源 behavior-行为 business-业务事件',
    event_type  VARCHAR(32)  NOT NULL COMMENT '事件类型',
    user_id     BIGINT       DEFAULT NULL COMMENT '用户ID',
    pet_id      BIGINT       DEFAULT NULL COMMENT '宠物ID',
    target_type VARCHAR(32)  DEFAULT NULL COMMENT '目标类型 product/space/ai_message/page',
    target_id   BIGINT       DEFAULT NULL COMMENT '目标ID',
    scene       VARCHAR(32)  DEFAULT NULL COMMENT '场景 home/shop/pet_chat',
    payload     TEXT         COMMENT '原始载荷(JSON)',
    event_time  DATETIME     DEFAULT NULL COMMENT '事件发生时间',
    create_time DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_event_id (event_id),
    KEY idx_user_id (user_id),
    KEY idx_source_type (source, event_type),
    KEY idx_event_time (event_time)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='数据中台ODS原始事件';

-- ------------------------------------------------------------
-- DWD：行为明细层
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_behavior
(
    id          BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    event_id    VARCHAR(64)  NOT NULL COMMENT '事件唯一ID(幂等键)',
    user_id     BIGINT       DEFAULT NULL COMMENT '用户ID',
    event_type  VARCHAR(32)  NOT NULL COMMENT 'exposure/click/search/ai_feedback/page_view/order/review',
    target_type VARCHAR(32)  DEFAULT NULL COMMENT '目标类型',
    target_id   BIGINT       DEFAULT NULL COMMENT '目标ID',
    scene       VARCHAR(32)  DEFAULT NULL COMMENT '场景',
    position    INT          DEFAULT NULL COMMENT '曝光/点击位置',
    keyword     VARCHAR(128) DEFAULT NULL COMMENT '搜索关键词',
    species     VARCHAR(32)  DEFAULT NULL COMMENT '关联物种',
    feedback    INT          DEFAULT 0 COMMENT 'AI反馈 1-有用 -1-无用 0-无',
    event_time  DATETIME     DEFAULT NULL COMMENT '事件发生时间',
    create_time DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_event_id (event_id),
    KEY idx_user_id (user_id),
    KEY idx_target (target_type, target_id),
    KEY idx_event_time (event_time),
    KEY idx_species (species)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='数据中台DWD行为明细';

-- ------------------------------------------------------------
-- DWS：用户画像/特征层
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_user_profile
(
    id                BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    user_id           BIGINT       NOT NULL COMMENT '用户ID(唯一)',
    own_species       VARCHAR(255) DEFAULT NULL COMMENT '饲养物种(逗号分隔)',
    species_affinity  TEXT         COMMENT '物种亲和度(JSON {物种:权重})',
    activity_level    VARCHAR(16)  DEFAULT 'low' COMMENT '活跃度 low/medium/high',
    consumption_level VARCHAR(16)  DEFAULT 'low' COMMENT '消费力 low/medium/high',
    exposure_cnt      BIGINT       DEFAULT 0 COMMENT '累计曝光',
    click_cnt         BIGINT       DEFAULT 0 COMMENT '累计点击',
    ctr               DOUBLE       DEFAULT 0 COMMENT '点击率',
    search_cnt        BIGINT       DEFAULT 0 COMMENT '累计搜索',
    ai_feedback_up    BIGINT       DEFAULT 0 COMMENT 'AI有用反馈数',
    ai_feedback_down  BIGINT       DEFAULT 0 COMMENT 'AI无用反馈数',
    last_active_time  DATETIME     DEFAULT NULL COMMENT '最近活跃时间',
    tags              VARCHAR(512) DEFAULT NULL COMMENT '标签(JSON数组)',
    feature_version   INT          DEFAULT 1 COMMENT '特征版本号',
    create_time       DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time       DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted           TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_id (user_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='数据中台DWS用户画像';

-- ------------------------------------------------------------
-- DWS：商品统计层
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_product_stats
(
    id           BIGINT   NOT NULL COMMENT '主键(雪花ID)',
    product_id   BIGINT   NOT NULL COMMENT '商品ID(唯一)',
    exposure_cnt BIGINT   DEFAULT 0 COMMENT '累计曝光',
    click_cnt    BIGINT   DEFAULT 0 COMMENT '累计点击',
    ctr          DOUBLE   DEFAULT 0 COMMENT '点击率',
    order_cnt    BIGINT   DEFAULT 0 COMMENT '累计下单',
    review_cnt   BIGINT   DEFAULT 0 COMMENT '累计评价',
    hot_score    DOUBLE   DEFAULT 0 COMMENT '热度分',
    create_time  DATETIME DEFAULT NULL COMMENT '创建时间',
    update_time  DATETIME DEFAULT NULL COMMENT '更新时间',
    deleted      TINYINT  DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_product_id (product_id),
    KEY idx_hot_score (hot_score)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='数据中台DWS商品统计';

-- ------------------------------------------------------------
-- ADS：按日指标层
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_metric_daily
(
    id           BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    stat_date    DATE         NOT NULL COMMENT '统计日期',
    metric_key   VARCHAR(64)  NOT NULL COMMENT '指标键',
    metric_value DOUBLE       DEFAULT 0 COMMENT '指标值',
    dim_key      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '维度键(默认空)',
    create_time  DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time  DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted      TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_date_key_dim (stat_date, metric_key, dim_key)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='数据中台ADS按日指标';

-- ------------------------------------------------------------
-- 反馈样本层（曝光⋈点击，供模型训练）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_sample
(
    id          BIGINT      NOT NULL COMMENT '主键(雪花ID)',
    user_id     BIGINT      NOT NULL COMMENT '用户ID',
    scene       VARCHAR(32) NOT NULL DEFAULT '' COMMENT '场景',
    target_type VARCHAR(32) NOT NULL DEFAULT '' COMMENT '目标类型',
    target_id   BIGINT      NOT NULL COMMENT '目标ID',
    position    INT         DEFAULT NULL COMMENT '曝光位置',
    exposed     TINYINT     DEFAULT 0 COMMENT '是否曝光 0/1',
    clicked     TINYINT     DEFAULT 0 COMMENT '是否点击 0/1',
    feedback    INT         DEFAULT 0 COMMENT 'AI反馈 1/-1/0',
    label       TINYINT     DEFAULT 0 COMMENT '训练标签(是否点击) 0/1',
    sample_date DATE        NOT NULL COMMENT '样本日期',
    event_time  DATETIME    DEFAULT NULL COMMENT '事件时间',
    create_time DATETIME    DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME    DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT     DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_sample (user_id, scene, target_type, target_id, sample_date),
    KEY idx_sample_date (sample_date)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='数据中台反馈样本';

-- ------------------------------------------------------------
-- 模型注册表（样本→训练→注册→服务 闭环）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_model
(
    id           BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    model_key    VARCHAR(32)  NOT NULL COMMENT '模型键 rerank_ctr',
    version      INT          NOT NULL COMMENT '版本号',
    algorithm    VARCHAR(32)  NOT NULL COMMENT '算法 logreg',
    features     VARCHAR(255) DEFAULT NULL COMMENT '特征名(JSON数组)',
    weights      TEXT         COMMENT '权重(JSON数组)',
    auc          DOUBLE       DEFAULT 0 COMMENT '留出集AUC',
    sample_count INT          DEFAULT 0 COMMENT '训练样本数',
    status       TINYINT      DEFAULT 0 COMMENT '状态 0-停用 1-激活',
    remark       VARCHAR(255) DEFAULT NULL COMMENT '备注',
    create_time  DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time  DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted      TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_key_version (model_key, version),
    KEY idx_key_status (model_key, status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='数据中台模型注册表';