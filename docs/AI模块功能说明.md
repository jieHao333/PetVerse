# PetVerse AI 模块功能说明

> 本文是 `ai-service`（Python / FastAPI / LangGraph）的功能说明书，覆盖**编排结构、接口契约、存储、配置、降级策略**五个方面。启动步骤与环境变量清单见 [ai-service/README.md](../ai-service/README.md)，本文侧重「模块内部是怎么组织的、为什么这么设计」。

## 1. 模块定位

`ai-service` 是 PetVerse 的 AI 能力微服务，与 8 个 Java 模块并列，通过网关统一入口 `/api/ai/**`（`StripPrefix=1`，服务内实际路径为 `/ai/**`）对外提供服务。

| 维度 | 说明 |
|---|---|
| 语言 / 框架 | Python 3.12 · FastAPI · LangGraph · LangChain |
| 端口 / 注册 | `127.0.0.1:8086`，启动后注册进 Nacos（网关 `lb://ai-service` 发现） |
| 模型接入 | 对话模型、Embedding、视觉模型、语音转写全部 **OpenAI 兼容格式**，改 `base_url + model + key` 即可切换 DeepSeek / 阿里云百炼等任意服务商 |
| 鉴权 | 不自行校验 JWT，信任网关统一鉴权后注入的 `X-User-Id` 请求头；缺失 / 非法一律 `{"code":401,"msg":"未登录"}` |
| 报文契约 | 与 Java 端 `Result<T>` 完全对齐：所有响应均为 **HTTP 200 + `{code,msg,data}`**，前端不会收到裸 5xx 或英文技术报错 |
| 存储 | 业务数据统一落 PostgreSQL（库 `petverse_ai`）；结果缓存热层用 Redis（db=3） |

**设计基调**：AI 对话链路长（浏览器 → 网关 → ai-service → LLM 服务商 → Redis / PostgreSQL），任一环节都可能抖动。模块的核心工程目标是「**短暂故障不显化为用户可见的错误**」——除知识检索外，几乎所有外部依赖都做了静默降级（详见第 7 节）。

## 2. 能力总览

四项能力各自用一张 LangGraph 状态图编排，节点职责单一、可观测、可扩展：

| 能力 | 入口接口 | 编排图 | 图结构 |
|---|---|---|---|
| AI 养宠顾问对话 | `POST /ai/chat/stream` | `app/graph/chat_graph.py` | 意图识别 →（RAG 检索 / 工具调用）→ 长期记忆读取 → Prompt 组装 → 生成 |
| AI 健康智能评估 | `POST /ai/health/assess` | `app/graph/health_graph.py` | 档案整理 → 分析 → 落库 |
| 商品评论摘要 | `POST /ai/shop/review/summary` | `app/graph/review_graph.py` | 拉取评论 → 摘要 → 写缓存 |
| 个性化推荐 | `GET /ai/recommend/feed` | `app/graph/recommend_graph.py` | 画像聚合 → 召回候选 → 重排 → 写缓存 |

四张图都在首次调用时 `compile()` 一次、进程内缓存复用（`get_*_graph()` 单例）。对话图编译时额外注入两个官方持久化组件：**checkpoint saver**（会话级短期记忆）与 **runtime store**（跨会话长期记忆），见第 5 节。

## 3. 对话编排结构（chat_graph）

对话是模块最复杂的部分。整体是一张带条件分支的状态图：

```
START → classify_intent ──┬─(knowledge/health/medical_urgent)─→ retrieve_knowledge ─┬─(tool_query)─→ tool_action ─┐
                          ├─(tool_query)───────────────────────────────────────────┴────────────────────────────┤
                          └─(chitchat)───────────────────────────────────────────────────────────────────────→ recall_memory → compose → generate → END
```

图状态 `ChatState`（`TypedDict`）中，`messages` 通道走 `add_messages` reducer 跨轮累积，其余字段后写覆盖前值。

### 3.1 classify_intent（意图识别）

- **真实模式**：用 LLM 结构化输出（`IntentResult`）把用户问题分为 5 类意图；
- **mock 模式 / LLM 失败**：降级为关键词规则（`_rule_intent`），保证分类永远有结果。
- **纯附件消息**（只有图片 / 音频 / 视频、无文字）：没有可分类文本，直接归 `knowledge`（养宠场景发图绝大多数是「看看这是什么情况」）。

| 意图 | 含义 | 后续分支 |
|---|---|---|
| `chitchat` | 打招呼、闲聊 | 直接 → recall_memory |
| `knowledge` | 养宠知识（喂养 / 疫苗 / 驱虫 / 行为 / 护理） | → retrieve_knowledge（RAG） |
| `health` | 健康评估、体检、养护 | → retrieve_knowledge（RAG） |
| `medical_urgent` | 疑似疾病 / 用药 / 急症（中毒、抽搐、尿闭等） | → retrieve_knowledge（RAG）+ 强制就医护栏 |
| `tool_query` | 需查询用户本人数据（宠物 / 订单 / 购物车 / 评论 / 动态） | → tool_action（Agent 工具） |

### 3.2 retrieve_knowledge（RAG 检索）

- 命中 `knowledge / health / medical_urgent` 且 `RAG_ENABLED=true` 时，用 **pgvector + OpenAI 兼容 Embedding** 做语义检索；
- 余弦相似度（`1 - distance`）+ 阈值过滤（`RAG_SCORE_THRESHOLD`，默认 0.35）+ Top-K（默认 4），结果带**来源标注**拼进 Prompt，供回答引用、降低幻觉；
- **不做关键词降级**：Embedding 未配置或向量库不可用时直接抛 `RagUnavailable`，由路由层转成 `error` 事件。取舍理由——知识问答里「无依据却答得像真的」比明确失败更糟。
- 知识库语料为 `app/knowledge/*.md`（疫苗 / 驱虫 / 喂养 / 常见病 / 行为护理），由 `scripts/ingest_knowledge.py` 切分后全量幂等写入 pgvector。

### 3.3 tool_action（Agent 工具调用）

- 意图为 `tool_query` 且非 mock 时，用 `langchain.agents.create_agent` 起一个 **ReAct Agent**，让模型自主决定调用哪些工具查询用户真实数据；
- 工具集（`app/tools.py`，全部只读）：`get_my_pets` / `get_my_orders` / `get_my_cart` / `get_product_reviews` / `get_hot_posts` / `search_products`；
- **用户隔离**：工具按请求动态构建（闭包捕获 `user_id`），`user_id<=0` 直接返回空工具列表，天然杜绝越权查询；
- 工具返回内容截断到 2000 字符再入上下文；任何工具失败都降级为空，不影响对话主流程。

### 3.4 recall_memory（长期记忆读取）

- 从 LangGraph 官方 **runtime store**（`config["store"]`）读取**用户级 + 当前宠物级**长期记忆，渲染为 bullet 行注入 Prompt；
- 命名空间两级隔离：用户级 `(petverse, user_mem, uid)`、宠物级 `(petverse, pet_mem, uid, petId)`；
- 记忆条数按 `MEMORY_MAX_ITEMS`（默认 20）截断，控制 token 成本；
- `MEMORY_ENABLED=false` / store 未注入 / 读取失败 → 降级为空字符串，对话不受影响。

### 3.5 compose（Prompt 组装）

纯函数（`app/persona.py`），把多路上下文拼成最终 System Prompt + 消息列表：

```
System Prompt = 养宠顾问人设
              + 宠物档案块（物种/品种/年龄/体重/疫苗/病史…，仅拼非空字段）
              + 长期记忆块（recall_memory 渲染的偏好/习性）
              + 参考知识块（RAG 检索结果，带来源）
              + 用户数据块（工具查到的真实订单/购物车/评论…）
              + 医疗护栏（命中 medical_urgent 时强制附加「尽快就医、勿自行用药」）
```

- **历史窗口**：从图状态 `messages` 取最近 `HISTORY_MAX_MESSAGES`（默认 40）条，checkpoint 保留全量记忆、此处按窗口裁剪控制 token；
- **多模态**：仅**当前轮**用户消息携带 `image_url` 分片发给视觉模型；历史轮附件早已以文字描述并入消息内容，不再重复发图（既省 token，也避免图片分片进 checkpoint 撑爆记忆存储）。

### 3.6 generate（生成）

- 真实模式调用 LLM 流式生成，并把助手回复写回图状态 `messages`（随 checkpoint 自动持久化，成为下一轮的跨轮记忆）；空输出不落记忆；
- 最终消息含图片分片时自动切换**视觉模型客户端**（`get_vision_client`）；
- mock 模式不在图内生成，由路由层走打字机 mock 流，再手动补写回图状态保持两模式记忆一致。

### 3.7 流式输出与归一化

路由层用 `graph.astream(stream_mode="messages")` 消费图内所有 LLM 调用的消息流，但**只透传 `generate` 节点**（按 `metadata.langgraph_node` 过滤）的助手文本增量——意图识别、工具 Agent 的中间产物不会漏给前端。

## 4. 其余三张编排图

### 4.1 health_graph（健康评估）

`load_profile → analyze → persist`

- `load_profile`：整理宠物档案 + 健康字段（体重 / BCS / 驱虫 / 特殊时期 / 疫苗 / 养育方式 / 病史），标记缺失项；
- `analyze`：真实模式用 LLM 结构化输出 `PetHealthReport`（评分 0-100、评级、风险、建议、养护计划、提醒、免责声明）；mock / LLM 失败降级为**规则评估**（按档案完整度与风险词给分）；
- `persist`：报告落 PostgreSQL `pet_health_report`（按用户 + 宠物保留历史），失败仅记日志。
- **安全边界**：Prompt 明确「不得做医疗诊断，仅基于用户填写信息评估，有就医必要时在 risks 中提示」。

### 4.2 review_graph（评论摘要）

`fetch_reviews → summarize → persist_cache`

- 经 `clients.biz` 直连 shop-service 拉取商品真实评论（最多 40 条参与、单条截断 300 字）；
- 真实模式 LLM 结构化输出 `ReviewSummary`（情感 / 一句话总结 / 优缺点 / 关键词 / 条数），优缺点强制来自评论内容不得编造；mock / 少评论降级为规则摘要（评分均值定情感 + n-gram 词频抽关键词）；
- 结果写两级缓存（key `ai:review:summary:{productId}`，TTL 默认 6 小时）；
- **单飞（single_flight）**：同一商品并发未命中只放行一次真实计算，其余等锁后直接取缓存，避免热点商品在缓存过期瞬间被并发打穿（多次 LLM 调用）。

### 4.3 recommend_graph（个性化推荐）

`gather_profile → recall_candidates → rerank → persist_cache`

- `gather_profile`：**五路并发**拉取用户画像（宠物 / 订单 / 购物车 / 评价 / 我的动态），`return_exceptions=True` 任一失败为空、不影响其余；
- `recall_candidates`：画像关键词（宠物物种 / 品种 + 交互过的商品名）搜索商品 + 泛化在售商品 + 热门动态，去重规范化为候选；
- `rerank`：真实模式 LLM 结合画像重排并生成推荐理由（只能用候选列表中出现过的 id，不得编造），mock / 失败降级为启发式排序（商品热销优先、动态按点赞）；
- `persist_cache`：按用户 + 场景写两级缓存（key `ai:recommend:{scene}:{userId}`，TTL 默认 10 分钟），`refresh=true` 跳过缓存强制刷新；同样用单飞防打穿。

## 5. 记忆与存储设计

### 5.1 两类记忆（都用 LangGraph 官方组件 + PostgreSQL）

| 记忆 | 组件 | 粒度 | 生命周期 | 写入时机 |
|---|---|---|---|---|
| **会话记忆**（短期） | 官方 checkpoint `PostgresSaver` | `thread_id = petverse-chat:{userId}:{sessionId}` | 与会话绑定，删会话即删记忆 | 每个超级步自动持久化图状态 `messages` |
| **长期记忆**（跨会话） | 官方 runtime store `PostgresStore` | 用户级 / 宠物级命名空间 | 跨会话保留，与删会话解耦 | 每轮正常结束后**后台任务**抽取写入 |

**会话记忆的关键设计——「暂停不再失忆」**：用户点「停止生成」或客户端中途断开时，中断发生在 `generate` 落盘之前。路由层在 `asyncio.shield` 保护下，把「用户问题 + 已产出的部分回复（标记 `interrupted`）」经 `aupdate_state(as_node="generate")` 补写回图状态，被中止的轮次同样进入后续对话的记忆；同时 PostgreSQL 侧 `save_turn_if_exists` 兜底保存（会话已删则原子跳过，不复活历史），**用户消息无条件落库**（即使中止时零产出，首轮提问也不丢）。

**长期记忆的抽取引擎**（`app/longterm.py`）：每轮 `done` 之后后台跑一次——读取该用户现有记忆清单 → LLM 结构化输出 `add/update/delete` 操作列表 → 逐条应用到 store。防抖靠 `update`（LLM 看到既有清单，重复或演进的信息复用原 key 覆盖，不重复堆积）；只记长期有效的养宠信息（偏好 / 习性 / 病史），一次性提问细节（「今天体温 39 度正常吗」）不进记忆；单条 ≤50 字、单轮 ≤8 条操作；全程异常静默、mock 模式跳过。

### 5.2 PostgreSQL 表（库 `petverse_ai`）

| 表 | 用途 | 创建方式 |
|---|---|---|
| `chat_session` | 会话（按用户 + 宠物隔离，一只宠物可建多会话） | 服务启动幂等自动建表 |
| `chat_message` | 消息（`interrupted` 标记中止、`attachments` JSONB 存多模态附件） | 服务启动幂等自动建表 |
| `pet_health_report` | 健康评估报告（按用户 + 宠物保留历史） | `db/schema_pgvector.sql` |
| `ai_cache` | AI 结果缓存温层（Redis 缺失时兜底） | `db/schema_pgvector.sql` |
| `checkpoints` 等 | LangGraph checkpoint（会话记忆） | 官方 `setup()` 自动建表 |
| store 表 | LangGraph runtime store（长期记忆） | 官方 `setup()` 自动建表 |
| `langchain_pg_collection` / `langchain_pg_embedding` | pgvector 向量库（RAG 语料） | LangChain PGVector 自动管理 |

会话与消息是**前端历史展示 / 会话管理**的持久层；checkpoint 承载**LLM 上下文记忆**——两者定位不同、互不替代，同库不同表。

### 5.3 Redis（db=3）

- **结果缓存热层**：评论摘要 / 推荐（经 `app/cache.py` 两级门面读写）；
- **用户级并发计数**：多实例共享的单用户并发额度（Lua 脚本 `INCR/DECR` 原子操作 + TTL 兜底回收）。

### 5.4 两级结果缓存（app/cache.py）

```
读：Redis（热，快）未命中 → 回落 ai_cache（温，PG 持久）→ 都未命中返回 None 重算
写：Redis 与 ai_cache 并行双写（TTL 一致，各 0~10% 随机抖动防雪崩）
```

- **为什么要第二级**：Redis 是易失热缓存，实例故障 / 重启会让全部结果缓存失效、请求透传重算（LLM 调用 + 外部服务查询）；ai_cache 落 PostgreSQL 与业务数据同库持久，Redis 缺失期间缓存能力不受单点影响；
- **ai_cache 命中不回填 Redis**：避免「回填续期」让 TTL 语义失真（推荐缓存「10 分钟自然过期重建」的意图会被持续访问无限延长）——温层定位是「Redis 缺失时仍可用」，不是保温；
- 任何缓存异常都内部静默降级，主流程不受影响（恢复后 redis-py 自动重连）。

## 6. 接口契约

所有接口经网关 `/api/ai/**`（StripPrefix=1）访问，需登录（网关注入 `X-User-Id`）。

| 方法 | 网关路径 | 说明 |
|---|---|---|
| POST | `/api/ai/chat/stream` | SSE 流式对话；体 `{message, pet, sessionId?, attachments?}`；首帧 `meta` 回传 sessionId |
| POST | `/api/ai/chat/upload` | 上传多模态附件（图片 / 音频 / 视频）直传 OSS，返回附件信息 |
| GET | `/api/ai/chat/history?sessionId=` | 查询会话历史（PostgreSQL，时间正序） |
| GET | `/api/ai/chat/sessions` | 用户全部会话（跨宠物，最近活跃在前，每条带 petId） |
| DELETE | `/api/ai/chat/sessions/{id}` | 删除会话（连带消息与 checkpoint 记忆） |
| GET | `/api/ai/chat/memories` | 查询当前用户全部长期记忆（用户级 + 所有宠物级） |
| DELETE | `/api/ai/chat/memories/{key}?scope=&petId=` | 删除单条长期记忆（命名空间天然隔离越权） |
| POST | `/api/ai/health/assess` | 宠物健康评估；体为宠物档案（含 `health`），返回结构化报告 |
| GET | `/api/ai/health/history?petId=&limit=` | 宠物历史健康评估（时间倒序） |
| POST | `/api/ai/shop/review/summary` | 商品评论摘要；体 `{productId}` |
| GET | `/api/ai/recommend/feed?scene=home\|shop&refresh=` | 个性化推荐流 |

**SSE 事件序列**：`meta`（回传 sessionId）→ `delta` × n（文本增量）→ `done`；异常时发 `error` 后结束流。服务端每 15s 发一行注释心跳 `: ping`（防代理 idle 超时断连）。

**统一报文**：成功 `{"code":200,"msg":"success","data":...}`；失败 `{"code":xxx,"msg":"...","data":null}`（401 未登录 / 400 参数错误 / 404 会话不存在 / 429 并发超限 / 500 服务开小差）。参数校验失败由全局 `RequestValidationError` 处理器统一转 `{"code":400,"msg":"参数错误"}`，与 Java 端 `GlobalExceptionHandler` 行为对齐。

**雪花 ID 精度**：`petId` 等 19 位雪花 ID 一律以**字符串**下发，避免超过 JS Number 安全整数（2^53）被前端截断。

## 7. 配置说明（.env）

配置由 `pydantic-settings` 从 `ai-service/.env` 读取，`app/config.py` 的 `Settings` 单例集中暴露。首次使用复制 `.env.example` 为 `.env`。

| 配置组 | 关键项 | 说明 |
|---|---|---|
| 对话模型 | `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | OpenAI 兼容；Key 留空或 `MOCK_CHAT=true` 走 mock。`DEEPSEEK_*` 为向后兼容别名（`LLM_*` 未配时自动回落） |
| Embedding | `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | RAG 向量化，**知识检索必需**；不配置则知识类提问直接报错。维度须与 pgvector 一致 |
| 视觉 / 语音 | `LLM_VISION_*` / `ASR_*` | 多模态：图片理解、音频转写；Key/Base 缺省回落到 `LLM_*`；未配置则降级为文字占位提示 |
| OSS | `OSS_ENDPOINT` / `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_BUCKET_NAME` / `OSS_DOMAIN` | 附件直传，与 Java 侧共用同一桶 |
| PostgreSQL | `PG_HOST` / `PG_PORT` / `PG_USER` / `PG_PASSWORD` / `PG_DB` / `PG_POOL_*` | 会话 / 消息 / checkpoint / store / RAG / 健康报告 / 缓存温层 |
| Redis | `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | 结果缓存热层 + 并发计数（db=3） |
| RAG | `RAG_ENABLED` / `RAG_TOP_K` / `RAG_SCORE_THRESHOLD` / `RAG_COLLECTION` | 检索开关与参数 |
| 记忆 | `HISTORY_MAX_MESSAGES` / `MEMORY_ENABLED` / `MEMORY_MAX_ITEMS` | 上下文窗口条数、长期记忆开关与注入上限 |
| 缓存 TTL | `REVIEW_SUMMARY_TTL` / `RECOMMEND_CACHE_TTL` | 评论摘要 6 小时、推荐 10 分钟 |
| 生成参数 | `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` | 默认 512 / 0.8 |
| 业务服务地址 | `PET_SERVICE_URL` / `SPACE_SERVICE_URL` / `SHOP_SERVICE_URL` … | ai-service 直连各微服务拉上下文，注入内部 `X-User-Id`；`HTTP_TIMEOUT` 默认 8s |
| 多模态上限 | `CHAT_MAX_ATTACHMENTS` / `MEDIA_MAX_IMAGE_MB` / `MEDIA_MAX_AUDIO_MB` / `MEDIA_MAX_VIDEO_MB` | 附件数与单文件大小上限 |

派生开关（`Settings` property）：`is_mock`（无 Key 或强制 mock）、`embedding_enabled`、`vision_enabled`、`asr_enabled`、`oss_configured`——各能力据此自动选择真实链路或降级链路。

## 8. 降级与韧性策略

这是 AI 模块工程量最集中的部分，分四层。

### 8.1 服务过载保护（ai-service）

- **全局并发闸**：同一时刻最多 20 路流式对话（`asyncio.Semaphore`），3s 内拿不到信号量即返回过载提示；
- **单用户并发闸**：同一用户最多 2 路并发（防脚本刷满全局闸挤占他人）；额度计数走 **Redis 共享**（多实例生效，Lua 原子 `INCR/DECR`），首次建立设 300s TTL 兜底回收进程异常退出未归还的额度；**Redis 不可用退化为进程内计数**，单实例上限仍生效；
- **消息长度上限**：单条 ≤2000 字、附件 ≤4 个，保护上下文窗口与 token 成本；
- **LLM 首帧重试**：流在产出任何内容前失败（服务商瞬时抖动、429）时自动重建流静默重试一轮，用户无感知；已产出内容后不重试，避免重复输出。

### 8.2 存储层降级与自愈

- **结果缓存两级降级**：见 5.4，Redis 故障时读走 ai_cache、写仅落 ai_cache，仅两级都不可用才退化为重算；
- **checkpoint / runtime store（记忆）**：官方 `PostgresSaver` / `PostgresStore`（同步引擎）+ **线程适配层**——本服务跑在 Windows 上，psycopg 异步连接在默认 `ProactorEventLoop` 下不可用，故异步方法统一经 `asyncio.to_thread` 代理执行；PG 故障时读降级为「无历史 / 无长期记忆」、写静默失败，对话不受影响；建表失败带 30s 冷却重试，PG 恢复后**无需重启服务**即可重新启用记忆；
- **会话与消息持久化**：读降级为空历史、写仅记日志；连接池用 `psycopg_pool`（惰性连接 + 自有断线重连与坏连接淘汰），PG 重启 / 网络闪断后自愈；业务表缺失时带 30s 冷却自动补建；
- **会话操作（建 / 删 / 查）**：失败**向上抛**，由路由层转成统一业务错误报文（不静默降级）——会话是对话的前提。

### 8.3 外部依赖降级

- **LLM 不可用 / 无 Key**：`MOCK_CHAT` 走内置打字机 mock 回复，编排逻辑（意图 / 检索 / 工具 / 组装）照常执行，便于联调；
- **结构化输出跨服务商降级**：OpenAI 兼容服务对结构化输出支持差异大（DeepSeek 思考模型既不支持 `json_schema` 也不支持 `tool_choice`），按 `json_schema → function_calling → json_mode`（附显式 Schema 提示）顺序尝试并缓存首次成功方式；
- **RAG 不可用**：唯一**不降级**的链路——直接报错（见 3.2）；
- **视觉 / 语音未配置**：图片 / 音频降级为文字占位提示，引导用户文字补充，不阻断对话；
- **业务服务调用失败**（`clients.py`）：服务未启动 / 超时 / 报文异常一律返回空并仅记日志，推荐与健康评估在数据缺失时退化为通用结果；
- **Nacos 注册**：SDK 优先、OpenAPI 降级，外加自建 daemon 心跳线程每 5s 保活；检测到实例丢失（beat 返回 `20404` / 连续失败）自动重注册（30s 节流）；注册失败只打日志、绝不阻断启动。

### 8.4 断连与异常收尾

- **SSE 心跳**：15s 无 token 产出即发注释行 ping；
- **客户端断开 / 用户停止生成**：问题与部分回复补写进 checkpoint + PostgreSQL（见 5.1），`asyncio.shield` 保证取消路径的写入落地，正常路径已收尾则不重复补写；
- **生产者-消费者解耦**：LLM / mock 流先搬进 `asyncio.Queue`，消费端对「等待下一个片段」做超时心跳，而不必直接对 async generator 用 `wait_for`（超时取消会破坏生成器状态）；
- **统一报文契约**：所有错误均为 HTTP 200 + `{code,msg,data}`。

### 8.5 各依赖缺失时的行为速查

| 未启动 / 未配置 | 影响 |
|---|---|
| LLM Key | 走 mock 打字机回复（编排照常） |
| Embedding | 知识类提问**直接报错**（不降级） |
| PostgreSQL | 会话持久化与对话记忆降级、知识检索报错、健康报告不落库 |
| Redis | 结果缓存退化为重算（ai_cache 温层仍可用）、并发额度降级为进程内计数 |
| 业务微服务 | 推荐 / 健康评估 / 工具查询退化为通用结果或空 |
| OSS | 多模态附件上传不可用 |
| Nacos | 服务照常启动，网关切走 `lb://ai-service` 发现（需直连或降级路由） |

## 9. 代码地图

```
ai-service/app/
├── main.py              FastAPI 实例 + lifespan（启动初始化各连接池 / Nacos 注册，停机释放）
├── config.py            pydantic-settings 配置单例（.env）
├── chat.py              对话路由（SSE 流式、并发闸、心跳、首帧重试、中断补写、附件上传、会话/记忆管理）
├── health.py / review.py / recommend.py   健康评估 / 评论摘要 / 推荐路由
├── graph/
│   ├── chat_graph.py        对话编排（意图/RAG/工具/记忆/组装/生成）
│   ├── health_graph.py      健康评估编排
│   ├── review_graph.py      评论摘要编排
│   └── recommend_graph.py   个性化推荐编排
├── llm.py               LLM / Embedding / 视觉客户端封装 + 跨服务商结构化输出 + mock 流
├── persona.py           System Prompt 组装（人设 + 档案 + 记忆 + 知识 + 数据 + 医疗护栏）
├── vectorstore.py       pgvector RAG 检索（余弦 + 阈值 + 来源；不可用抛 RagUnavailable）
├── tools.py             Agent 工具集（闭包捕获 user_id，只读，用户隔离）
├── checkpoint.py        LangGraph 官方 PostgresSaver + 线程适配 + 降级（会话记忆）
├── memstore.py          LangGraph 官方 PostgresStore + 线程适配 + 降级（长期记忆）
├── longterm.py          长期记忆抽取引擎（每轮后台 LLM 抽取 add/update/delete）
├── persistence.py       PostgreSQL 会话与消息持久层（幂等建表、事务删会话、中止兜底保存）
├── pg_store.py          PostgreSQL 健康报告 + ai_cache 温层缓存
├── cache.py             两级缓存门面（Redis 热 + ai_cache 温；单飞 + TTL 抖动）
├── memory.py            Redis 通用缓存与原子计数（Lua 脚本）
├── limits.py            用户级并发闸（Redis 共享计数 + 进程内兜底）
├── clients.py           业务微服务只读客户端（httpx，注入 X-User-Id，失败降级为空）
├── media.py             多模态附件（类型/大小校验、OSS 直传、音频转写、SSRF 防护）
├── oss.py               阿里云 OSS 封装
├── schemas.py           Pydantic 模型（请求/响应 + LLM 结构化输出 schema）
└── nacos_client.py      Nacos 注册（SDK + OpenAPI 双保险 + 自建心跳保活）
```
