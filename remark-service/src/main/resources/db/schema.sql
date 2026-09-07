-- petverse_remark 数据库脚本（点赞 + 评论等通用互动服务）
--
-- 全新环境初始化：先创建数据库，再执行本脚本全部建表语句
-- CREATE DATABASE IF NOT EXISTS petverse_remark DEFAULT CHARSET utf8mb4;

CREATE TABLE IF NOT EXISTS like_record
(
    id          BIGINT   NOT NULL COMMENT '主键(雪花ID)',
    target_type TINYINT  NOT NULL COMMENT '点赞对象类型 0-动态',
    target_id   BIGINT   NOT NULL COMMENT '被点赞对象ID',
    user_id     BIGINT   NOT NULL COMMENT '点赞用户ID',
    create_time DATETIME DEFAULT NULL COMMENT '点赞时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_target_user (target_type, target_id, user_id),
    KEY idx_user_id (user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='点赞记录(物理删除，支持取消后重新点赞)';

CREATE TABLE IF NOT EXISTS like_count
(
    target_type TINYINT    NOT NULL COMMENT '点赞对象类型 0-动态',
    target_id   BIGINT     NOT NULL COMMENT '被点赞对象ID',
    count       INT        NOT NULL DEFAULT 0 COMMENT '点赞数快照',
    update_time DATETIME DEFAULT NULL COMMENT '最近同步时间',
    PRIMARY KEY (target_type, target_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='点赞计数快照(Redis冷数据回填用)';

-- 通用评论（与点赞同构，以 target_type + target_id 定位被评论对象，当前用于圈子动态）
-- 平铺展示互动过程，reply_user_id 标记回复对象（为空表示直接评论动态）
CREATE TABLE IF NOT EXISTS comment
(
    id            BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    target_type   TINYINT      NOT NULL COMMENT '评论对象类型 0-圈子动态',
    target_id     BIGINT       NOT NULL COMMENT '被评论对象ID',
    user_id       BIGINT       NOT NULL COMMENT '评论人用户ID',
    reply_user_id BIGINT     DEFAULT NULL COMMENT '被回复人用户ID（为空表示直接评论对象）',
    content       VARCHAR(500) NOT NULL COMMENT '评论内容',
    create_time   DATETIME   DEFAULT NULL COMMENT '创建时间',
    update_time   DATETIME   DEFAULT NULL COMMENT '更新时间',
    deleted       TINYINT    DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_target (target_type, target_id, deleted),
    KEY idx_user_id (user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='通用评论(逻辑删除)';

-- 站内通知（事件驱动统一落库）：记录谁(actor_user_id)因某来源(source)对某对象(target_id)做了什么(type)，通知谁(user_id)
CREATE TABLE IF NOT EXISTS notification
(
    id           BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    user_id      BIGINT       NOT NULL COMMENT '收件人用户ID(被通知人)',
    actor_user_id BIGINT      DEFAULT NULL COMMENT '触发人用户ID',
    type         TINYINT      NOT NULL COMMENT '通知类型 1-评论 2-回复 3-点赞',
    source       TINYINT      NOT NULL COMMENT '通知来源 1-圈子动态 2-商品评价',
    target_id    BIGINT       DEFAULT NULL COMMENT '跳转目标对象ID(动态ID或商品ID)',
    content      VARCHAR(255) DEFAULT NULL COMMENT '内容摘要',
    is_read      TINYINT      NOT NULL DEFAULT 0 COMMENT '是否已读 0-未读 1-已读',
    create_time  DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time  DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted      TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_user_read (user_id, is_read, create_time)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='站内通知(逻辑删除)';
