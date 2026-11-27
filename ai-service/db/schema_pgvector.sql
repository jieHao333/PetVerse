
-- 向量扩展（服务初始化时也会尝试创建，此处显式建一次便于排查）
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
-- attachments 为图片附件 JSONB 数组，仅用户消息携带；refs 为回答引用的知识片段）
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
-- RAG 引用来源列（仅 assistant 消息携带，历史回放时还原「这条回答依据哪些文档」）
ALTER TABLE chat_message ADD COLUMN IF NOT EXISTS refs JSONB NOT NULL DEFAULT '[]'::jsonb;
COMMENT ON TABLE chat_message IS '宠物AI对话消息（按会话隔离的持久化存储）';
COMMENT ON COLUMN chat_message.user_id IS '所属用户ID';
COMMENT ON COLUMN chat_message.pet_id IS '所属宠物ID（缺失时兜底为0）';
COMMENT ON COLUMN chat_message.session_id IS '所属会话ID（chat_session.id）';
COMMENT ON COLUMN chat_message.role IS '角色：user / assistant';
COMMENT ON COLUMN chat_message.interrupted IS '助手回复是否被用户中止生成（仅 assistant 消息置位）';
COMMENT ON COLUMN chat_message.attachments IS '图片附件数组 [{type,url,key,mime,name,size}]（仅用户消息；type 当前恒为 image，url 为 OSS 公网地址，key 为 OSS 对象 key）';
CREATE INDEX IF NOT EXISTS idx_chat_message_user_session_time
    ON chat_message (user_id, session_id, create_time);
CREATE INDEX IF NOT EXISTS idx_chat_message_user_pet_time
    ON chat_message (user_id, pet_id, create_time);
-- 刻意不建 chat_message.session_id → chat_session.id 外键：
-- 历史迁移数据里可能存在 session_id=0 的孤儿消息，且删会话由应用层同时清理消息与 checkpoint；
-- 加外键会让这类存量数据直接写不进去。

-- ------------------------------------------------------------
-- LangGraph 对话记忆（checkpoint）
--   每个超级步写一行 checkpoints；通道值（消息、工具结果等）拆到 checkpoint_blobs；
--   checkpoint_writes 存并行分支的中间结果，用于中断恢复。
--   thread_id 形如 petverse-chat:<user_id>:<session_id>（见 app/checkpoint.py）。
--   这些表由 PostgresSaver.setup() 自动创建，以下为等价 DDL；
--   不要手工写入 checkpoint_migrations 版本号，留空让库自己补跑迁移。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkpoint_migrations
(
    v INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints
(
    thread_id            TEXT  NOT NULL,
    checkpoint_ns        TEXT  NOT NULL DEFAULT '',
    checkpoint_id        TEXT  NOT NULL,
    parent_checkpoint_id TEXT,
    type                 TEXT,
    checkpoint           JSONB NOT NULL,
    metadata             JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
COMMENT ON TABLE checkpoints IS 'LangGraph 对话记忆快照（按 thread 隔离，thread_id=petverse-chat:<user>:<session>）';
COMMENT ON COLUMN checkpoints.checkpoint_ns IS '命名空间（子图路径，顶层为空串）';
COMMENT ON COLUMN checkpoints.checkpoint IS '状态快照（通道 → 版本号的映射）';
COMMENT ON COLUMN checkpoints.metadata IS '写入元数据（来源节点、步号等）';

CREATE TABLE IF NOT EXISTS checkpoint_blobs
(
    thread_id     TEXT  NOT NULL,
    checkpoint_ns TEXT  NOT NULL DEFAULT '',
    channel       TEXT  NOT NULL,
    version       TEXT  NOT NULL,
    type          TEXT  NOT NULL,
    blob          BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);
COMMENT ON TABLE checkpoint_blobs IS 'LangGraph 通道值（消息 / 工具结果等按版本序列化存放）';

CREATE TABLE IF NOT EXISTS checkpoint_writes
(
    thread_id     TEXT    NOT NULL,
    checkpoint_ns TEXT    NOT NULL DEFAULT '',
    checkpoint_id TEXT    NOT NULL,
    task_id       TEXT    NOT NULL,
    idx           INTEGER NOT NULL,
    channel       TEXT    NOT NULL,
    type          TEXT,
    blob          BYTEA   NOT NULL,
    task_path     TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
COMMENT ON TABLE checkpoint_writes IS 'LangGraph 节点中间写入（人工审批挂起后从断点恢复靠它）';

-- 索引：库自身用 CREATE INDEX CONCURRENTLY 建同名索引；这里用普通写法，
-- 保证整份脚本可以放在事务里执行（CONCURRENTLY 不允许在事务块内）。
CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes (thread_id);

-- ------------------------------------------------------------
-- LangGraph 长期记忆 store
--   跨会话保存用户 / 宠物偏好，命名空间前缀 petverse.user_mem.<uid> /
--   petverse.pet_mem.<uid>.<pid>（见 app/memstore.py，库里把元组按点号拼成 prefix）。
--   同样由 PostgresStore.setup() 自动创建，以下为等价 DDL。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS store_migrations
(
    v INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS store
(
    prefix      TEXT                     NOT NULL,
    key         TEXT                     NOT NULL,
    value       JSONB                    NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP WITH TIME ZONE,
    ttl_minutes INTEGER,
    PRIMARY KEY (prefix, key)
);
COMMENT ON TABLE store IS 'LangGraph 长期记忆条目（prefix 为命名空间串，key 为条目 ID）';
COMMENT ON COLUMN store.prefix IS '命名空间前缀（点分串，如 petverse.user_mem.12）';
COMMENT ON COLUMN store.value IS '记忆内容 {content, kind, petName, createdAt, updatedAt}';
COMMENT ON COLUMN store.expires_at IS '过期时间（当前业务不使用 TTL，留空）';

-- text_pattern_ops 让「按前缀模糊查询」走索引；库里用 CONCURRENTLY 建，这里同前。
CREATE INDEX IF NOT EXISTS store_prefix_idx ON store USING btree (prefix text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_store_expires_at ON store (expires_at) WHERE expires_at IS NOT NULL;

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

-- ------------------------------------------------------------
-- 知识库：文档登记 + 知识片段（RAG 混合检索的存储层）
--   kb_chunk.tokens 为 jieba 分词结果（空格分隔），供进程内 BM25 关键词通道使用；
--   kb_meta.version 是 BM25 索引版本戳，任何写入都会自增，索引据此惰性重建。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_document
(
    doc_id      TEXT        PRIMARY KEY,
    name        TEXT        NOT NULL,
    source      TEXT        NOT NULL,
    kind        TEXT        NOT NULL,
    category    TEXT        NOT NULL DEFAULT '',
    species     TEXT[]      NOT NULL DEFAULT '{}',
    tags        TEXT[]      NOT NULL DEFAULT '{}',
    sha256      TEXT        NOT NULL DEFAULT '',
    origin      TEXT        NOT NULL DEFAULT 'repo',
    file_path   TEXT,
    chunk_count INT         NOT NULL DEFAULT 0,
    char_count  INT         NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 存量库补列（在线预览要回读原文，需要 file_path）
ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS file_path TEXT;
COMMENT ON TABLE kb_document IS '知识库文档登记（同名 doc_id 重复导入按覆盖处理）';
COMMENT ON COLUMN kb_document.file_path IS '原文位置：仓库文档为源文件绝对路径，上传文档为 KB_STORAGE_DIR 下的留存文件；在线预览与 PDF 回读用它';
COMMENT ON COLUMN kb_document.kind IS '文档类型：md / pdf / docx / doc（前端来源标签据此显示图标）';
COMMENT ON COLUMN kb_document.species IS '适用物种数组（猫/狗/通用/其他），检索阶段做物种降权或过滤';
COMMENT ON COLUMN kb_document.sha256 IS '文件内容哈希，导入脚本据此跳过未变更文档';
COMMENT ON COLUMN kb_document.origin IS '来源：repo（仓库语料目录）/ upload（后台上传接口）';

CREATE TABLE IF NOT EXISTS kb_chunk
(
    id          BIGSERIAL   PRIMARY KEY,
    doc_id      TEXT        NOT NULL REFERENCES kb_document (doc_id) ON DELETE CASCADE,
    chunk_index INT         NOT NULL,
    section     TEXT        NOT NULL DEFAULT '',
    category    TEXT        NOT NULL DEFAULT '',
    species     TEXT[]      NOT NULL DEFAULT '{}',
    content     TEXT        NOT NULL,
    tokens      TEXT        NOT NULL DEFAULT '',
    embedding   vector(1024) NOT NULL
);
COMMENT ON TABLE kb_chunk IS '知识库片段（正文已含「文档标题｜小节」上下文前缀）';
COMMENT ON COLUMN kb_chunk.tokens IS '片段分词（含标题/分类/标签扩展词），BM25 关键词通道用它建索引';
COMMENT ON COLUMN kb_chunk.embedding IS '片段向量，维度必须与 .env 的 EMBEDDING_DIM 一致';
CREATE INDEX IF NOT EXISTS idx_kb_chunk_doc ON kb_chunk (doc_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunk_embedding
    ON kb_chunk USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS kb_meta
(
    key   TEXT PRIMARY KEY,
    value BIGINT NOT NULL
);
COMMENT ON TABLE kb_meta IS '知识库元信息（version：BM25 索引版本戳，写入即自增）';
INSERT INTO kb_meta (key, value) VALUES ('version', 0) ON CONFLICT (key) DO NOTHING;

-- ------------------------------------------------------------
-- AI 扩展配置（管理员在「AI 扩展管理」页维护，服务启动时也会自动建表）
--   ai_mcp_server   ：MCP 服务连接参数，启用中的服务其远程工具会并入对话 Agent；
--   ai_model_provider：模型供应商（协议 + Base URL + Key + 模型清单），
--                     一个供应商下可挂多个模型，与 ZCode / Zed 的「Provider + Model」一致；
--   ai_model_setting ：当前生效模型指针（全表恒只有 id=1 一行）。
--   注：Skill 技能不落数据库，改为磁盘标准技能包 skills/<name>/SKILL.md（见 app/skill_store.py）。
--       历史版本曾建 ai_skill 表，现已弃用（存量表可保留不动，服务不再读写）。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_mcp_server
(
    id          BIGSERIAL    PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL UNIQUE,
    transport   VARCHAR(16)  NOT NULL DEFAULT 'streamable_http',
    url         TEXT         NOT NULL DEFAULT '',
    command     TEXT         NOT NULL DEFAULT '',
    args        JSONB        NOT NULL DEFAULT '[]'::jsonb,
    env         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    headers     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    description VARCHAR(255) NOT NULL DEFAULT '',
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    create_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ai_mcp_server IS 'MCP 服务配置（管理员维护；启用中的服务其远程工具并入对话 Agent）';
COMMENT ON COLUMN ai_mcp_server.transport IS '传输协议：streamable_http / sse / stdio';
COMMENT ON COLUMN ai_mcp_server.url IS 'streamable_http / sse 的服务地址';
COMMENT ON COLUMN ai_mcp_server.command IS 'stdio 模式的启动命令（如 npx / uvx / python）';
COMMENT ON COLUMN ai_mcp_server.args IS 'stdio 模式的命令参数数组';
COMMENT ON COLUMN ai_mcp_server.env IS 'stdio 模式注入子进程的环境变量';
COMMENT ON COLUMN ai_mcp_server.headers IS 'HTTP 模式的自定义请求头（如 Authorization）';
COMMENT ON COLUMN ai_mcp_server.enabled IS '是否启用（停用后模型看不见该服务的工具）';

-- 模型供应商：同一供应商下的模型共享协议 / 地址 / Key，切换模型只改 ai_model_setting 指针。
-- 协议只有两种：openai = /chat/completions（地址一般带 /v1），
--               anthropic = /v1/messages（地址不带 /v1）。
CREATE TABLE IF NOT EXISTS ai_model_provider
(
    id          BIGSERIAL    PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL UNIQUE,
    protocol    VARCHAR(16)  NOT NULL DEFAULT 'openai',
    base_url    TEXT         NOT NULL DEFAULT '',
    api_key     TEXT         NOT NULL DEFAULT '',
    models      JSONB        NOT NULL DEFAULT '[]'::jsonb,
    description VARCHAR(255) NOT NULL DEFAULT '',
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    create_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ai_model_provider IS '模型供应商（管理员维护；协议 + Base URL + Key + 模型清单）';
COMMENT ON COLUMN ai_model_provider.protocol IS '协议：openai（OpenAI 兼容 /chat/completions）/ anthropic（/v1/messages）';
COMMENT ON COLUMN ai_model_provider.base_url IS '接口地址（已规整：Anthropic 去掉结尾 /v1，OpenAI 去掉误粘的 /chat/completions）';
COMMENT ON COLUMN ai_model_provider.api_key IS 'API Key 明文（与 .env 同级信任域；管理接口只回显尾号）';
COMMENT ON COLUMN ai_model_provider.models IS '模型清单 [{id,label,vision,maxTokens}]；vision=true 表示支持图片输入';
COMMENT ON COLUMN ai_model_provider.enabled IS '是否启用（停用后不能再被选为当前模型）';

-- 当前生效模型：单例行（id 恒为 1），删供应商时服务会把这里清回 (0, '')。
CREATE TABLE IF NOT EXISTS ai_model_setting
(
    id          SMALLINT     PRIMARY KEY,
    provider_id BIGINT       NOT NULL DEFAULT 0,
    model_id    VARCHAR(128) NOT NULL DEFAULT '',
    update_time TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ai_model_setting IS '当前生效模型指针（单例行 id=1；provider_id=0 表示回落到 .env 的 LLM_*）';
COMMENT ON COLUMN ai_model_setting.provider_id IS '生效模型所属供应商 ID（ai_model_provider.id）';
COMMENT ON COLUMN ai_model_setting.model_id IS '生效模型 ID（须在该供应商的 models 清单内）';
INSERT INTO ai_model_setting (id, provider_id, model_id) VALUES (1, 0, '')
    ON CONFLICT (id) DO NOTHING;
