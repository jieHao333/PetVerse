# PetVerse AI 服务（ai-service）

基于 FastAPI 的 AI 能力微服务，启动后注册进 Nacos（供网关 `lb://ai-service` 发现），通过网关统一入口 `/api/ai/**`（StripPrefix=1）对外提供服务。内部统一使用 **LangGraph** 编排，对话模型与 Embedding 均为 **OpenAI 兼容格式**（改 `base_url` + `model` 即可切换 DeepSeek / 阿里云百炼等任意服务商）。

## 能力总览

| 能力 | 说明 | 编排图 |
|---|---|---|
| AI 养宠顾问对话 | 意图识别 → RAG 检索 / 工具调用 → 生成，SSE 流式，多会话管理 | `app/graph/chat_graph.py` |
| AI 健康智能评估 | 基于宠物档案 + 健康信息生成评分、风险、养护计划、提醒 | `app/graph/health_graph.py` |
| 商品评论摘要 | 汇总真实评论生成口碑概览（情感 / 优缺点 / 关键词） | `app/graph/review_graph.py` |
| 个性化推荐 | 结合宠物画像、订单、购物车、评价、动态重排推荐 | `app/graph/recommend_graph.py` |

**对话编排（chat_graph）**：`意图识别 →（条件分支）知识检索 / 工具调用 → Prompt 组装 → 生成`。命中医疗 / 急症意图时，最终 Prompt 会强制附加「及时咨询专业宠物医生」的就医护栏。工具让 AI 能查询用户真实数据（我的宠物 / 订单 / 购物车 / 商品评论 / 热门动态）。

**RAG 检索**：首选 pgvector 语义检索（OpenAI 兼容 Embedding）；未配置 Embedding 或 pgvector 不可用时，自动降级为内置知识文件的字符 bigram 关键词检索，保证功能在无外部依赖时仍可演示。

**会话与存储**：对话按「用户 + 宠物 + 会话」三级隔离；Redis（db=3）作 LLM 短窗口上下文缓存，MySQL（`petverse_ai.chat_session` / `chat_message`）作持久层，两者并行写入；健康评估报告落 PostgreSQL（`petverse_ai.pet_health_report`）。

## 环境要求

- Python 3.12
- 可访问的 Nacos（默认 `localhost:8848`）
- 可访问的 Redis（默认 `localhost:6379`，密码 `123456`，db=3）
- 可访问的 MySQL（默认 `localhost:3306`，库 `petverse_ai`）
- 可访问的 PostgreSQL + **pgvector 插件**（默认 `localhost:5432`，库 `petverse_ai`；仅 RAG 语义检索 / 健康报告持久化需要，缺失时自动降级）
- LLM API Key（OpenAI 兼容，可选：留空或 `MOCK_CHAT=true` 时走内置 mock 回复）
- Embedding API Key（OpenAI 兼容，可选：不配置则 RAG 走关键词降级）

## 初始化数据库

```powershell
# MySQL：会话与消息表
mysql -uroot -p123456 < db/schema.sql
# PostgreSQL：pgvector 扩展 + 健康报告 / 缓存表
psql -U postgres -d petverse_ai -f db/schema_pgvector.sql
```

## 安装依赖

```powershell
.venv\Scripts\pip install -r requirements.txt
# 网络较慢时：-i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 配置说明（.env）

首次使用请复制 `.env.example` 为 `.env`。LLM / Embedding 均为 OpenAI 兼容格式：

| 配置项 | 说明 |
|---|---|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 对话模型（OpenAI 兼容）。DeepSeek：`https://api.deepseek.com/v1` + `deepseek-chat`；阿里云百炼：`https://dashscope.aliyuncs.com/compatible-mode/v1` + `qwen-plus`。留空 Key 或 `MOCK_CHAT=true` 时走 mock |
| `DEEPSEEK_*` | 兼容旧配置：`LLM_*` 留空时自动回落 |
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | RAG 向量化（OpenAI 兼容）。阿里云百炼 `text-embedding-v3`（dim 支持 1024/768/512）。**不配置则 RAG 自动降级为关键词检索** |
| `MOCK_CHAT` | `true` 强制内置模拟回复，无需真实调用 |
| `PG_*` | PostgreSQL + pgvector（RAG 向量库 / 健康报告） |
| `RAG_ENABLED` / `RAG_TOP_K` / `RAG_SCORE_THRESHOLD` | RAG 开关与检索参数 |
| `*_SERVICE_URL` | 各业务微服务地址（ai-service 直连拉取上下文，注入内部 `X-User-Id`） |
| `REVIEW_SUMMARY_TTL` / `RECOMMEND_CACHE_TTL` / `HEALTH_CACHE_TTL` | 各能力结果缓存 TTL（秒） |
| `HISTORY_MAX_MESSAGES` / `HISTORY_TTL_SECONDS` | Redis 对话上下文窗口与过期时间 |
| `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` | 生成参数 |

> pgvector 维度必须与 `EMBEDDING_DIM` 一致；切换 Embedding 模型需重建向量集合。

## 导入知识库（RAG 语料）

知识文档位于 `app/knowledge/*.md`（疫苗 / 驱虫 / 喂养 / 常见病 / 行为护理）。

```powershell
.venv\Scripts\python -m scripts.ingest_knowledge
```

- 配置了 Embedding：切分后全量写入 pgvector（幂等，可反复执行，以文件为唯一事实来源）。
- 未配置 Embedding：脚本提示跳过；对话检索直接读取原文件做关键词匹配。

## 启动服务

```powershell
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8086
```

## 接口列表

| 方法 | 网关路径（前端调用） | 说明 |
|---|---|---|
| POST | `/api/ai/chat/stream` | SSE 流式对话；体 `{"message","pet":{...},"sessionId"}`；首帧 `meta` 回传 sessionId |
| GET | `/api/ai/chat/history?sessionId=` | 查询会话历史（MySQL，时间正序） |
| GET | `/api/ai/chat/sessions` | 用户全部会话（跨宠物，最近活跃在前） |
| DELETE | `/api/ai/chat/sessions/{id}` | 删除会话（连带消息与 Redis 缓存） |
| POST | `/api/ai/health/assess` | 宠物健康评估；体为宠物档案（含 `health`），返回结构化报告 |
| GET | `/api/ai/health/history?petId=` | 宠物历史健康评估 |
| POST | `/api/ai/shop/review/summary` | 商品评论摘要；体 `{"productId"}` |
| GET | `/api/ai/recommend/feed?scene=home\|shop&refresh=` | 个性化推荐流 |

说明：

- 网关统一 JWT 鉴权后注入 `X-User-Id`；缺失 / 非法返回 `{"code":401,"msg":"未登录"}`。所有业务响应为 HTTP 200 + `{code,msg,data}`。
- SSE 事件：`meta` → `delta` * n → `done`；异常时 `error` 后结束流。
- 推荐 / 评论摘要 / 健康评估结果分别按用户、商品缓存于 Redis，`refresh=true` 可跳过推荐缓存强制刷新。
- 中文模型配置与 pgvector 初始化完成后，即自动启用语义检索；否则关键词降级仍可工作。

## 安全提示

- `.env` 含真实 API Key，请勿提交到仓库（本仓库仅提交 `.env.example`）。
- 若曾提交过 Key，请立即在服务商控制台轮换。
