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

## 生产环境韧性设计

AI 对话链路长（浏览器 → 网关 → ai-service → LLM 服务商 → Redis / MySQL / PostgreSQL），
任一环节都可能出问题。以下机制保证「短暂故障不显化为用户可见的错误」，分四层：

### 1. 网络层（前端）

- **连接阶段自动重连**：`fetch` 尚未收到响应头就失败（WiFi 切换、网络闪断）时，
  带退避（1s / 2s）自动重试最多 2 次，期间轻提示「正在自动重连」；
  已收到响应头则不再自动重试——后端可能已建会话 / 已开始生成，重发会造成重复。
- **连接看门狗（10s）**：`fetch` 挂起（TCP 半开、代理不回包）超过 10s 主动中断并按网络故障重试。
- **流看门狗（25s）**：服务端每 15s 发 SSE 心跳，连续 25s 无任何字节说明链路静默中断，
  主动 abort 并提示，避免界面永远卡在「生成中」。
- **失败气泡 + 重新生成**：失败时保留气泡与错误说明，一键重发本轮消息，不重打字。

### 2. 服务过载保护（ai-service）

- **全局并发闸**：同一时刻最多 20 路流式对话，超过时 3s 内拿不到信号量即返回过载提示。
- **单用户并发闸**：同一用户最多 2 路并发（防脚本刷满全局闸挤占其他用户）。
- **消息长度上限**：单条消息最长 2000 字，保护上下文窗口与 token 成本。
- **LLM 首帧重试**：流在产出任何内容前失败（LLM 服务商瞬时抖动、429）时，
  自动重建流静默重试一轮，用户无感知；已产出内容后不重试，避免重复输出。

### 3. 存储层降级与自愈

- **Redis 上下文缓存**：故障时读降级为空、写静默失败，对话主流程不受影响；
  Redis 恢复后自动重连（redis-py 内建）。
- **MySQL 消息持久化**：读降级为空历史、写仅记日志；**连接池惰性重建**——启动时
  MySQL 未就绪不再永久不可用，后续请求带 5s 冷却重试重建，MySQL 恢复后无需重启服务；
  连接级异常时把坏连接显式淘汰出池，坏连接不会反复回池挨个坑请求。
- **会话操作（建 / 删 / 查）**：失败向上抛，由路由层转成统一业务错误报文（不静默降级）。

### 4. 断连与异常收尾

- **SSE 心跳**：15s 无 token 产出即发注释行 ping，防止网关 / 代理 idle 超时断连。
- **客户端中途断开**：已生成的部分回复经 `append_if_exists` / `save_turn_if_exists`
  原子化兜底落库（会话已被删则跳过，不复活历史；`asyncio.shield` 保证取消路径写入落地）。
- **统一报文契约**：所有错误均为 HTTP 200 + `{code,msg,data}`，前端不会收到裸 5xx 或英文技术报错。
