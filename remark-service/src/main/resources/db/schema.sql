-- petverse_remark 数据库脚本（点赞服务）
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
