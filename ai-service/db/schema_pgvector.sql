-- petverse_ai PostgreSQL 初始化脚本（业务表 + pgvector 向量库）
--
-- 用途：为 ai-service 提供全部业务持久化能力——对话会话 / 消息、LangGraph
--       checkpoint 记忆、RAG 语义检索、AI 结果表（原先的 MySQL 库已整体迁移至此）。
-- 前置：PostgreSQL 已安装 pgvector 扩展（CREATE EXTENSION vector 需要该插件）。
-- 执行：psql -U postgres -d petverse_ai -f db/schema_pgvector.sql
--       （数据库 petverse_ai 需先存在：CREATE DATABASE petverse_ai;）
--
-- 说明：
--   1. 本脚本幂等（IF NOT EXISTS），可反复执行；
--   2. chat_session / chat_message 与 checkpoint 表（checkpoints / checkpoint_blobs /
--      checkpoint_writes / checkpoint_migrations）均由服务启动时自动创建，本文件
--      保留等价 DDL 作为手动执行入口与文档；
--   3. 知识库向量表由 LangChain 的 PGVector 自动管理
--      （langchain_pg_collection / langchain_pg_embedding），无需手工建表。

-- 向量扩展（PGVector 初始化时也会尝试创建，此处显式建一次便于排查）
CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------
-- 对话会话表（按「用户 + 宠物」隔离，一只宠物可建多个会话）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_session
(
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL,
    pet_id      BIGINT      NOT NULL DEFAULT 0,
    title       VARCHAR(64) NOT NULL DEFAULT '新会话',
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE chat_session IS '宠物AI咨询会话（按用户+宠物隔离，支持多会话）';
COMMENT ON COLUMN chat_session.user_id IS '所属用户ID';
COMMENT ON COLUMN chat_session.pet_id IS '所属宠物ID（缺失时兜底为0）';
COMMENT ON COLUMN chat_session.title IS '会话标题（取首条用户消息，缺省为“新会话”）';
COMMENT ON COLUMN chat_session.update_time IS '最近活跃时间（每轮对话写入时刷新）';
CREATE INDEX IF NOT EXISTS idx_chat_session_user_pet_update
    ON chat_session (user_id, pet_id, update_time DESC);

-- ------------------------------------------------------------
-- 对话消息表（按会话隔离；interrupted 标记被用户中止生成的回复；
-- attachments 为多模态附件 JSONB 数组，仅用户消息携带）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_message
(
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL,
    pet_id      BIGINT      NOT NULL DEFAULT 0,
    session_id  BIGINT      NOT NULL DEFAULT 0,
    role        VARCHAR(16) NOT NULL,
    content     TEXT        NOT NULL,
    interrupted BOOLEAN     NOT NULL DEFAULT FALSE,
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 多模态附件列（幂等：存量库补列，新建表由上方 CREATE 语句保证缺失——此处统一补齐）
ALTER TABLE chat_message ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'::jsonb;
COMMENT ON TABLE chat_message IS '宠物AI对话消息（按会话隔离的持久化存储）';
COMMENT ON COLUMN chat_message.user_id IS '所属用户ID';
COMMENT ON COLUMN chat_message.pet_id IS '所属宠物ID（缺失时兜底为0）';
COMMENT ON COLUMN chat_message.session_id IS '所属会话ID（chat_session.id）';
COMMENT ON COLUMN chat_message.role IS '角色：user / assistant';
COMMENT ON COLUMN chat_message.interrupted IS '助手回复是否被用户中止生成（仅 assistant 消息置位）';
COMMENT ON COLUMN chat_message.attachments IS '多模态附件数组 [{type,url,mime,name,size,transcript?}]（仅用户消息）';
CREATE INDEX IF NOT EXISTS idx_chat_message_user_session_time
    ON chat_message (user_id, session_id, create_time);
CREATE INDEX IF NOT EXISTS idx_chat_message_user_pet_time
    ON chat_message (user_id, pet_id, create_time);

-- ------------------------------------------------------------
-- AI 健康评估报告（按用户 + 宠物隔离，保留历史多次评估）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pet_health_report
(
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL,
    pet_id      BIGINT      NOT NULL DEFAULT 0,
    score       INT         NOT NULL DEFAULT 0,
    level       VARCHAR(16) NOT NULL DEFAULT 'unknown',
    payload     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pet_health_report IS '宠物 AI 健康评估报告';
COMMENT ON COLUMN pet_health_report.user_id IS '所属用户ID';
COMMENT ON COLUMN pet_health_report.pet_id IS '所属宠物ID';
COMMENT ON COLUMN pet_health_report.score IS '健康评分 0-100';
COMMENT ON COLUMN pet_health_report.level IS '评级：excellent/good/fair/warning';
COMMENT ON COLUMN pet_health_report.payload IS '完整评估报告（risks/suggestions/care_plan/reminders）';
CREATE INDEX IF NOT EXISTS idx_health_user_pet_time
    ON pet_health_report (user_id, pet_id, create_time DESC);

-- ------------------------------------------------------------
-- AI 通用结果缓存（评论摘要 / 推荐等落库缓存，Redis 缺失时的兜底）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_cache
(
    cache_key   VARCHAR(191) PRIMARY KEY,
    payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    expire_time TIMESTAMPTZ  NOT NULL
);
COMMENT ON TABLE ai_cache IS 'AI 结果缓存（可选，Redis 缺失时兜底）';
COMMENT ON COLUMN ai_cache.cache_key IS '缓存键（如 review:summary:123）';
CREATE INDEX IF NOT EXISTS idx_ai_cache_expire ON ai_cache (expire_time);
