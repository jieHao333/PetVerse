-- 向量扩展（服务初始化时也会尝试创建，此处显式建一次便于排查）
CREATE EXTENSION IF NOT EXISTS vector;

create table if not exists chat_session
(
    id          bigserial,
    user_id     bigint                                                       not null,
    pet_id      bigint                   default 0                           not null,
    title       varchar(64)              default '新会话'::character varying not null,
    create_time timestamp with time zone default now()                       not null,
    update_time timestamp with time zone default now()                       not null,
    primary key (id)
);

comment on table chat_session is '宠物AI咨询会话（按用户+宠物隔离，支持多会话）';

comment on column chat_session.user_id is '所属用户ID';

comment on column chat_session.pet_id is '所属宠物ID（缺失时兜底为0）';

comment on column chat_session.title is '会话标题（取首条用户消息，缺省为“新会话”）';

comment on column chat_session.update_time is '最近活跃时间（每轮对话写入时刷新）';

create index if not exists idx_chat_session_user_pet_update
    on chat_session (user_id asc, pet_id asc, update_time desc);

create table if not exists chat_message
(
    id          bigserial,
    user_id     bigint                                       not null,
    pet_id      bigint                   default 0           not null,
    session_id  bigint                   default 0           not null,
    role        varchar(16)                                  not null,
    content     text                                         not null,
    interrupted boolean                  default false       not null,
    create_time timestamp with time zone default now()       not null,
    attachments jsonb                    default '[]'::jsonb not null,
    refs        jsonb                    default '[]'::jsonb not null,
    primary key (id)
);

comment on table chat_message is '宠物AI对话消息（按会话隔离的持久化存储）';

comment on column chat_message.user_id is '所属用户ID';

comment on column chat_message.pet_id is '所属宠物ID（缺失时兜底为0）';

comment on column chat_message.session_id is '所属会话ID（chat_session.id）';

comment on column chat_message.role is '角色：user / assistant';

comment on column chat_message.interrupted is '助手回复是否被用户中止生成（仅 assistant 消息置位）';

comment on column chat_message.attachments is '多模态附件数组 [{type,url,key,mime,name,size,transcript?}]（仅用户消息；url 为 OSS 公网地址，key 为 OSS 对象 key）';

create index if not exists idx_chat_message_user_session_time
    on chat_message (user_id, session_id, create_time);

create index if not exists idx_chat_message_user_pet_time
    on chat_message (user_id, pet_id, create_time);

create table if not exists pet_health_report
(
    id          bigserial,
    user_id     bigint                                                        not null,
    pet_id      bigint                   default 0                            not null,
    score       integer                  default 0                            not null,
    level       varchar(16)              default 'unknown'::character varying not null,
    payload     jsonb                    default '{}'::jsonb                  not null,
    create_time timestamp with time zone default now()                        not null,
    primary key (id)
);

comment on table pet_health_report is '宠物 AI 健康评估报告';

comment on column pet_health_report.user_id is '所属用户ID';

comment on column pet_health_report.pet_id is '所属宠物ID';

comment on column pet_health_report.score is '健康评分 0-100';

comment on column pet_health_report.level is '评级：excellent/good/fair/warning';

comment on column pet_health_report.payload is '完整评估报告（risks/suggestions/care_plan/reminders）';

create index if not exists idx_health_user_pet_time
    on pet_health_report (user_id asc, pet_id asc, create_time desc);

create table if not exists ai_cache
(
    cache_key   varchar(191)              not null,
    payload     jsonb default '{}'::jsonb not null,
    expire_time timestamp with time zone  not null,
    primary key (cache_key)
);

comment on table ai_cache is 'AI 结果缓存（可选，Redis 缺失时兜底）';

comment on column ai_cache.cache_key is '缓存键（如 review:summary:123）';

create index if not exists idx_ai_cache_expire
    on ai_cache (expire_time);

create table if not exists kb_document
(
    doc_id      text                                          not null,
    name        text                                          not null,
    source      text                                          not null,
    kind        text                                          not null,
    category    text                     default ''::text     not null,
    species     text[]                   default '{}'::text[] not null,
    tags        text[]                   default '{}'::text[] not null,
    sha256      text                     default ''::text     not null,
    origin      text                     default 'repo'::text not null,
    file_path   text,
    chunk_count integer                  default 0            not null,
    char_count  integer                  default 0            not null,
    created_at  timestamp with time zone default now()        not null,
    updated_at  timestamp with time zone default now()        not null,
    primary key (doc_id)
);

comment on table kb_document is '知识库文档登记（同名 doc_id 重复导入按覆盖处理）';

comment on column kb_document.kind is '文档类型：md / pdf / docx / doc（前端来源标签据此显示图标）';

comment on column kb_document.species is '适用物种数组（猫/狗/通用/其他），检索阶段做物种降权或过滤';

comment on column kb_document.sha256 is '文件内容哈希，导入脚本据此跳过未变更文档';

comment on column kb_document.origin is '来源：repo（仓库语料目录）/ upload（后台上传接口）';

comment on column kb_document.file_path is '原文位置：仓库文档为源文件绝对路径，上传文档为 KB_STORAGE_DIR 下的留存文件；在线预览与 PDF 回读用它';

create table if not exists kb_chunk
(
    id          bigserial,
    doc_id      text                        not null,
    chunk_index integer                     not null,
    section     text   default ''::text     not null,
    category    text   default ''::text     not null,
    species     text[] default '{}'::text[] not null,
    content     text                        not null,
    tokens      text   default ''::text     not null,
    embedding   vector(1024)                not null,
    primary key (id),
    foreign key (doc_id) references kb_document
        on delete cascade
);

comment on table kb_chunk is '知识库片段（正文已含「文档标题｜小节」上下文前缀）';

comment on column kb_chunk.tokens is '片段分词（含标题/分类/标签扩展词），BM25 关键词通道用它建索引';

comment on column kb_chunk.embedding is '片段向量，维度必须与 .env 的 EMBEDDING_DIM 一致';

create index if not exists idx_kb_chunk_doc
    on kb_chunk (doc_id);

create index if not exists idx_kb_chunk_embedding
    on kb_chunk using hnsw (embedding vector_cosine_ops);

create table if not exists kb_meta
(
    key   text   not null,
    value bigint not null,
    primary key (key)
);

comment on table kb_meta is '知识库元信息（version：BM25 索引版本戳，写入即自增）';

create table if not exists checkpoint_blobs
(
    thread_id     text                  not null,
    checkpoint_ns text default ''::text not null,
    channel       text                  not null,
    version       text                  not null,
    type          text                  not null,
    blob          bytea,
    primary key (thread_id, checkpoint_ns, channel, version)
);

create index if not exists checkpoint_blobs_thread_id_idx
    on checkpoint_blobs (thread_id);

create table if not exists checkpoint_migrations
(
    v integer not null,
    primary key (v)
);

create table if not exists checkpoint_writes
(
    thread_id     text                  not null,
    checkpoint_ns text default ''::text not null,
    checkpoint_id text                  not null,
    task_id       text                  not null,
    idx           integer               not null,
    channel       text                  not null,
    type          text,
    blob          bytea                 not null,
    task_path     text default ''::text not null,
    primary key (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

create index if not exists checkpoint_writes_thread_id_idx
    on checkpoint_writes (thread_id);

create table if not exists checkpoints
(
    thread_id            text                      not null,
    checkpoint_ns        text  default ''::text    not null,
    checkpoint_id        text                      not null,
    parent_checkpoint_id text,
    type                 text,
    checkpoint           jsonb                     not null,
    metadata             jsonb default '{}'::jsonb not null,
    primary key (thread_id, checkpoint_ns, checkpoint_id)
);

create index if not exists checkpoints_thread_id_idx
    on checkpoints (thread_id);

create table if not exists langchain_pg_collection
(
    uuid      uuid    not null,
    name      varchar not null,
    cmetadata json,
    primary key (uuid),
    unique (name)
);

create table if not exists langchain_pg_embedding
(
    id            varchar not null,
    collection_id uuid,
    embedding     vector(1024),
    document      varchar,
    cmetadata     jsonb,
    primary key (id),
    foreign key (collection_id) references langchain_pg_collection
        on delete cascade
);

create index if not exists ix_cmetadata_gin
    on langchain_pg_embedding using gin (cmetadata jsonb_path_ops);

create unique index if not exists ix_langchain_pg_embedding_id
    on langchain_pg_embedding (id);

create table if not exists store
(
    prefix      text  not null,
    key         text  not null,
    value       jsonb not null,
    created_at  timestamp with time zone default CURRENT_TIMESTAMP,
    updated_at  timestamp with time zone default CURRENT_TIMESTAMP,
    expires_at  timestamp with time zone,
    ttl_minutes integer,
    primary key (prefix, key)
);

create index if not exists idx_store_expires_at
    on store (expires_at)
    where (expires_at IS NOT NULL);

create index if not exists store_prefix_idx
    on store (prefix text_pattern_ops);

create table if not exists store_migrations
(
    v integer not null,
    primary key (v)
);

create table if not exists ai_mcp_server
(
    id          bigserial,
    name        varchar(64)                                                           not null,
    transport   varchar(16)              default 'streamable_http'::character varying not null,
    url         text                     default ''::text                             not null,
    command     text                     default ''::text                             not null,
    args        jsonb                    default '[]'::jsonb                          not null,
    env         jsonb                    default '{}'::jsonb                          not null,
    headers     jsonb                    default '{}'::jsonb                          not null,
    description varchar(255)             default ''::character varying                not null,
    enabled     boolean                  default true                                 not null,
    create_time timestamp with time zone default now()                                not null,
    update_time timestamp with time zone default now()                                not null,
    primary key (id),
    unique (name)
);

comment on table ai_mcp_server is 'MCP 服务配置（管理员维护；启用中的服务其远程工具并入对话 Agent）';

comment on column ai_mcp_server.transport is '传输协议：streamable_http / sse / stdio';

comment on column ai_mcp_server.url is 'streamable_http / sse 的服务地址';

comment on column ai_mcp_server.command is 'stdio 模式的启动命令（如 npx / uvx / python）';

comment on column ai_mcp_server.args is 'stdio 模式的命令参数数组';

comment on column ai_mcp_server.env is 'stdio 模式注入子进程的环境变量';

comment on column ai_mcp_server.headers is 'HTTP 模式的自定义请求头（如 Authorization）';

comment on column ai_mcp_server.enabled is '是否启用（停用后模型看不见该服务的工具）';

create table if not exists ai_model_provider
(
    id          bigserial,
    name        varchar(64)                                                  not null,
    protocol    varchar(16)              default 'openai'::character varying not null,
    base_url    text                     default ''::text                    not null,
    api_key     text                     default ''::text                    not null,
    models      jsonb                    default '[]'::jsonb                 not null,
    description varchar(255)             default ''::character varying       not null,
    enabled     boolean                  default true                        not null,
    create_time timestamp with time zone default now()                       not null,
    update_time timestamp with time zone default now()                       not null,
    primary key (id),
    unique (name)
);

create table if not exists ai_model_setting
(
    id          smallint                                               not null,
    provider_id bigint                   default 0                     not null,
    model_id    varchar(128)             default ''::character varying not null,
    update_time timestamp with time zone default now()                 not null,
    primary key (id)
);

