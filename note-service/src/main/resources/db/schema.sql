-- petverse_note 数据库初始化脚本
-- 使用方式：先创建数据库，再执行本脚本
-- CREATE DATABASE IF NOT EXISTS petverse_note DEFAULT CHARSET utf8mb4;

CREATE TABLE IF NOT EXISTS note
(
    id          BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    title       VARCHAR(100) NOT NULL COMMENT '笔记标题',
    content     TEXT         NOT NULL COMMENT '笔记内容',
    category    VARCHAR(50)  DEFAULT NULL COMMENT '笔记分类',
    pet_id      BIGINT       DEFAULT NULL COMMENT '关联的宠物ID，可为空',
    user_id     BIGINT       NOT NULL COMMENT '所属用户ID',
    create_time DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_user_id (user_id),
    KEY idx_pet_id (pet_id),
    KEY idx_create_time (create_time)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='用户笔记';
