-- petverse_ai 数据库初始化脚本
-- ai-service 使用：将用户与宠物 AI 的对话消息持久化到 MySQL，
-- Redis（db=3）仍作为 LLM 短窗口上下文缓存保留，两者并行写入。

CREATE DATABASE IF NOT EXISTS petverse_ai
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

USE petverse_ai;

CREATE TABLE IF NOT EXISTS chat_message
(
    id          BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    user_id     BIGINT       NOT NULL COMMENT '所属用户ID',
    pet_id      BIGINT       NOT NULL DEFAULT 0 COMMENT '所属宠物ID（缺失时兜底为0）',
    role        VARCHAR(16)  NOT NULL COMMENT '角色：user / assistant',
    content     MEDIUMTEXT   NOT NULL COMMENT '消息内容',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_user_pet_time (user_id, pet_id, create_time)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='宠物AI对话消息（持久化存储）';
