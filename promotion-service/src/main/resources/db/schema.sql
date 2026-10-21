-- petverse_promotion 数据库初始化脚本
-- CREATE DATABASE IF NOT EXISTS petverse_promotion DEFAULT CHARSET utf8mb4;

-- 促销活动（形式多种多样，例如优惠券；当前预留扩展）
CREATE TABLE IF NOT EXISTS promotion
(
    id          BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    name        VARCHAR(50)  DEFAULT NULL COMMENT '活动名称',
    type        TINYINT      DEFAULT 1 COMMENT '活动类型 1-优惠券 2-分销',
    hot         TINYINT      DEFAULT 0 COMMENT '是否热门活动 0-否 1-是',
    begin_time  DATETIME     DEFAULT NULL COMMENT '活动开始时间',
    end_time    DATETIME     DEFAULT NULL COMMENT '活动结束时间',
    create_time DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='促销活动';

-- 优惠券（规则信息，金额字段单位为分）
CREATE TABLE IF NOT EXISTS coupon
(
    id                 BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    `name`             VARCHAR(20)  NOT NULL COMMENT '优惠券名称',
    type               TINYINT      DEFAULT 1 COMMENT '优惠券类型 1-普通券(保留)',
    discount_type      TINYINT      NOT NULL COMMENT '折扣类型 1-每满减 2-折扣 3-无门槛 4-满减',
    `specific`         TINYINT      DEFAULT 0 COMMENT '是否限定使用范围 0-否 1-是',
    discount_value     INT          NOT NULL COMMENT '折扣值：满减为抵扣金额(分)，打折为折扣率(80表示8折)',
    threshold_amount   INT          DEFAULT 0 COMMENT '使用门槛(分)，0表示无门槛',
    max_discount_amount INT         DEFAULT 0 COMMENT '最高优惠金额(分)，0表示无限制',
    obtain_way         TINYINT      NOT NULL COMMENT '获取方式 1-手动领取 2-兑换码',
    issue_begin_time   DATETIME     DEFAULT NULL COMMENT '开始发放时间',
    issue_end_time     DATETIME     DEFAULT NULL COMMENT '结束发放时间',
    term_days          INT          DEFAULT 0 COMMENT '有效期天数，0表示指定有效期',
    term_begin_time    DATETIME     DEFAULT NULL COMMENT '有效期开始时间',
    term_end_time      DATETIME     DEFAULT NULL COMMENT '有效期结束时间',
    status             TINYINT      DEFAULT 1 COMMENT '状态 1-待发放 2-未开始 3-发放中 4-已结束 5-暂停',
    total_num          INT          NOT NULL DEFAULT 1 COMMENT '总数量，不超过5000',
    issue_num          INT          DEFAULT 0 COMMENT '已发行数量，用于判断是否超发',
    used_num           INT          DEFAULT 0 COMMENT '已使用数量',
    user_limit         INT          DEFAULT 1 COMMENT '每人限领数量',
    ext_param          VARCHAR(255) DEFAULT NULL COMMENT '拓展参数(保留)',
    create_time        DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time        DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted            TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_status (status)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='优惠券';

-- 优惠券作用范围（分类或指定商品）
CREATE TABLE IF NOT EXISTS coupon_scope
(
    id        BIGINT   NOT NULL AUTO_INCREMENT COMMENT '主键(自增)',
    type      TINYINT  DEFAULT 1 COMMENT '范围类型 1-商品分类 2-指定商品',
    coupon_id BIGINT   NOT NULL COMMENT '优惠券id',
    biz_id    BIGINT   NOT NULL COMMENT '范围业务id（分类id或商品id）',
    PRIMARY KEY (id),
    KEY idx_coupon_id (coupon_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='优惠券作用范围';

-- 用户优惠券（领取记录，真正使用的优惠券信息）
CREATE TABLE IF NOT EXISTS user_coupon
(
    id              BIGINT   NOT NULL COMMENT '主键(雪花ID)',
    user_id         BIGINT   NOT NULL COMMENT '优惠券拥有者',
    coupon_id       BIGINT   NOT NULL COMMENT '优惠券模板id',
    term_begin_time DATETIME DEFAULT NULL COMMENT '有效期开始时间',
    term_end_time   DATETIME DEFAULT NULL COMMENT '有效期结束时间',
    used_time       DATETIME DEFAULT NULL COMMENT '使用时间（核销时间）',
    status          TINYINT  DEFAULT 1 COMMENT '状态 1-未使用 2-已使用 3-已过期',
    create_time     DATETIME DEFAULT NULL COMMENT '创建时间',
    update_time     DATETIME DEFAULT NULL COMMENT '更新时间',
    deleted         TINYINT  DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_user_id (user_id),
    KEY idx_coupon_id (coupon_id),
    KEY idx_user_status (user_id, status)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='用户优惠券';

-- 兑换码（主键为全局递增序列号，兑换码由序列号+优惠券id编码混淆生成）
CREATE TABLE IF NOT EXISTS exchange_code
(
    id                 INT      NOT NULL COMMENT '兑换码序列号(Redis自增生成)',
    code               VARCHAR(10) NOT NULL COMMENT '兑换码(Base32编码)',
    status             TINYINT  DEFAULT 1 COMMENT '状态 1-待兑换 2-已兑换 3-兑换活动已结束',
    user_id            BIGINT   DEFAULT NULL COMMENT '兑换人',
    type               TINYINT  DEFAULT 1 COMMENT '兑换类型 1-优惠券(保留)',
    exchange_target_id BIGINT   NOT NULL COMMENT '兑换目标id（优惠券id）',
    create_time        DATETIME DEFAULT NULL COMMENT '创建时间',
    expired_time       DATETIME DEFAULT NULL COMMENT '兑换码过期时间',
    update_time        DATETIME DEFAULT NULL COMMENT '更新时间',
    PRIMARY KEY (id),
    KEY idx_target_status (exchange_target_id, status)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='兑换码';
