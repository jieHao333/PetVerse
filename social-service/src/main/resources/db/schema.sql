-- petverse_social 数据库初始化脚本
-- 使用方式：先创建数据库，再执行本脚本
-- CREATE DATABASE IF NOT EXISTS petverse_social DEFAULT CHARSET utf8mb4;

-- 【存量库迁移】已存在 chat_message 表的环境，执行以下 SQL 支持文件消息：
-- ALTER TABLE chat_message ADD COLUMN msg_type TINYINT DEFAULT 0 COMMENT '消息类型 0-文本 1-图片 2-文件' AFTER content;
-- ALTER TABLE chat_message ADD COLUMN file_name VARCHAR(255) DEFAULT NULL COMMENT '文件原始名称' AFTER msg_type;

CREATE TABLE IF NOT EXISTS friend_request
(
    id           BIGINT   NOT NULL COMMENT '主键(雪花ID)',
    from_user_id BIGINT   NOT NULL COMMENT '发起申请的用户ID',
    to_user_id   BIGINT   NOT NULL COMMENT '接收申请的用户ID',
    status       TINYINT  DEFAULT 0 COMMENT '申请状态 0-待处理 1-已同意 2-已拒绝',
    create_time  DATETIME DEFAULT NULL COMMENT '创建时间',
    update_time  DATETIME DEFAULT NULL COMMENT '更新时间',
    deleted      TINYINT  DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_to_user_status (to_user_id, status),
    KEY idx_from_user (from_user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='好友申请';

CREATE TABLE IF NOT EXISTS friendship
(
    id             BIGINT   NOT NULL COMMENT '主键(雪花ID)',
    user_id        BIGINT   NOT NULL COMMENT '用户ID',
    friend_user_id BIGINT   NOT NULL COMMENT '好友的用户ID',
    create_time    DATETIME DEFAULT NULL COMMENT '创建时间',
    update_time    DATETIME DEFAULT NULL COMMENT '更新时间',
    deleted        TINYINT  DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_friend (user_id, friend_user_id),
    KEY idx_friend_user (friend_user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='好友关系(每对好友双向两条记录)';

CREATE TABLE IF NOT EXISTS chat_message
(
    id          BIGINT        NOT NULL COMMENT '主键(雪花ID)',
    sender_id   BIGINT        NOT NULL COMMENT '发送者用户ID',
    receiver_id BIGINT        NOT NULL COMMENT '接收者用户ID',
    content     VARCHAR(500)  NOT NULL COMMENT '消息内容：文本存文本，图片/文件存OSS地址',
    msg_type    TINYINT       DEFAULT 0 COMMENT '消息类型 0-文本 1-图片 2-文件',
    file_name   VARCHAR(255)  DEFAULT NULL COMMENT '文件原始名称',
    create_time DATETIME      DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME      DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT       DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_sender_receiver (sender_id, receiver_id, create_time),
    KEY idx_receiver (receiver_id, create_time)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='聊天消息';
