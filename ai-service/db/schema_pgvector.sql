-- petverse_ai PostgreSQL 初始化脚本（pgvector 向量库 + AI 业务扩展表）
--
-- 用途：为 ai-service 提供 RAG 语义检索与 AI 结果持久化能力。
-- 前置：PostgreSQL 已安装 pgvector 扩展（CREATE EXTENSION vector 需要该插件）。
-- 执行：psql -U postgres -d petverse_ai -f db/schema_pgvector.sql
--       （数据库 petverse_ai 需先存在：CREATE DATABASE petverse_ai;）
--
-- 说明：知识库向量表由 LangChain 的 PGVector 自动管理
--       （langchain_pg_collection / langchain_pg_embedding），无需手工建表，
--       这里只负责创建 vector 扩展与 AI 业务结果表。

-- 向量扩展（PGVector 初始化时也会尝试创建，此处显式建一次便于排查）
CREATE EXTENSION IF NOT EXISTS vector;

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
