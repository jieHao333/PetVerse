-- petverse_ai 数据库初始化脚本
-- ai-service 使用：将用户与 AI 养宠顾问的对话按「用户 + 宠物 + 会话」三级隔离持久化到 MySQL，
-- Redis（db=3）仍作为 LLM 短窗口上下文缓存保留（按会话隔离），两者并行写入。

CREATE DATABASE IF NOT EXISTS petverse_ai
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

USE petverse_ai;

-- 对话会话表：一个用户的每只宠物可创建多个会话
CREATE TABLE IF NOT EXISTS chat_session
(
    id          BIGINT      NOT NULL AUTO_INCREMENT COMMENT '会话ID',
    user_id     BIGINT      NOT NULL COMMENT '所属用户ID',
    pet_id      BIGINT      NOT NULL DEFAULT 0 COMMENT '所属宠物ID（缺失时兜底为0）',
    title       VARCHAR(64) NOT NULL DEFAULT '新会话' COMMENT '会话标题（取首条用户消息，缺省为“新会话”）',
    create_time DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最近活跃时间',
    PRIMARY KEY (id),
    KEY idx_user_pet_update (user_id, pet_id, update_time)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='宠物AI咨询会话（按用户+宠物隔离，支持多会话）';

CREATE TABLE IF NOT EXISTS chat_message
(
    id          BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    user_id     BIGINT       NOT NULL COMMENT '所属用户ID',
    pet_id      BIGINT       NOT NULL DEFAULT 0 COMMENT '所属宠物ID（缺失时兜底为0）',
    session_id  BIGINT       NOT NULL DEFAULT 0 COMMENT '所属会话ID（chat_session.id）',
    role        VARCHAR(16)  NOT NULL COMMENT '角色：user / assistant',
    content     MEDIUMTEXT   NOT NULL COMMENT '消息内容',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_user_session_time (user_id, session_id, create_time),
    KEY idx_user_pet_time (user_id, pet_id, create_time)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='宠物AI对话消息（按会话隔离的持久化存储）';

-- ------------------------------------------------------------
-- 旧库升级（已按早期版本建过 chat_message 的库执行一次即可）：
-- ALTER TABLE chat_message
--     ADD COLUMN session_id BIGINT NOT NULL DEFAULT 0 COMMENT '所属会话ID（chat_session.id）' AFTER pet_id,
--     ADD KEY idx_user_session_time (user_id, session_id, create_time);
-- 旧数据 session_id=0，不属于任何会话，可在确认后自行清理：
-- DELETE FROM chat_message WHERE session_id = 0;
-- ------------------------------------------------------------
