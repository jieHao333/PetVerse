# PetVerse AI 模块功能说明

> 本文档描述 `ai-service`（Python / FastAPI）的功能范围、编排结构、接口契约、数据存储与容错策略。
> 面向读者：项目使用者、二次开发者、面试评审。启动与部署见 [../ai-service/README.md](../ai-service/README.md)。

## 一、模块定位

AI 模块是整个平台的智能能力层，**独立部署为 Python 微服务**，与 Java 服务同构接入现有基础设施：

- **统一入口**：注册进 Nacos（服务名 `ai-service`），由 Spring Cloud Gateway 以 `lb://ai-service` + `Path=/api/ai/**` 路由，`StripPrefix=1` 后到达本服务；
- **统一鉴权**：网关完成 JWT 校验后剥离外部伪造头、注入 `X-User-Id`，本服务直接读取该头识别用户，**无需自行实现一套鉴权**；
- **统一报文**：所有业务响应为 HTTP 200 + `{code, msg, data}`，与 Java 侧 `GlobalExceptionHandler` 行为对齐，前端一套错误处理逻辑通吃；
- **反向调用**：本服务按需直连各 Java 服务（`/pet/my-list`、`/shop/order/page` 等），复用同一套内部信任头机制拉取用户真实数据。

```
前端 ──▶ 网关(/api/ai/** + JWT) ──▶ ai-service :8086
                                      │
                                      ├──▶ Java 业务服务（注入 X-User-Id 拉取宠物/订单/购物车/评论/动态）
                                      ├──▶ LLM 服务商（OpenAI 兼容：DeepSeek / 阿里云百炼 / ...）
                                      ├──▶ Embedding 服务商（RAG 向量化）
                                      ├──▶ PostgreSQL + pgvector（会话消息 / 对话记忆 / 长期记忆 / 知识库 / 报告 / 缓存）
                                      ├──▶ Redis（结果缓存热层 / 跨实例并发计数）
                                      └──▶ 阿里云 OSS（多模态附件）
```

## 二、技术选型

| 分类 | 选型 | 说明 |
|---|---|---|
| Web 框架 | FastAPI + Uvicorn | 原生 async，SSE 流式输出友好 |
| 编排框架 | **LangGraph**（StateGraph / checkpoint / runtime store） | 显式状态图编排，替代手写流程 |
| 模型接入 | langchain-openai（ChatOpenAI / OpenAIEmbeddings） | **OpenAI 兼容格式**，改 `base_url` + `model` 即可切换服务商 |
| 向量库 | PostgreSQL + **pgvector**（langchain-postgres `PGVector`） | 与业务数据同库，少维护一个中间件 |
| 业务持久化 | psycopg3 同步连接池 + `asyncio.to_thread` | 规避 Windows `ProactorEventLoop` 对 psycopg 异步连接的限制 |
| 缓存与并发计数 | redis-py（asyncio，db=3） | 结果缓存热层；单用户并发额度计数跨实例共享 |
| 对象存储 | oss2（阿里云 OSS） | 多模态附件直传 |
| 注册中心 | nacos-sdk-python + OpenAPI 兜底 | 双保险注册与心跳保活 |
| 配置 | pydantic-settings（`.env`） | 集中式、可校验、支持兼容回落 |

## 三、能力总览

| # | 能力 | 编排图 | 入口接口 |
|---|---|---|---|
| 1 | AI 养宠顾问对话（SSE 流式） | `app/graph/chat_graph.py` | `POST /api/ai/chat/stream` |
| 2 | 宠物健康智能评估 | `app/graph/health_graph.py` | `POST /api/ai/health/assess` |
| 3 | 商品评论智能摘要 | `app/graph/review_graph.py` | `POST /api/ai/shop/review/summary` |
| 4 | 个性化推荐流 | `app/graph/recommend_graph.py` | `GET /api/ai/recommend/feed` |
| 5 | 多模态附件上传 | —（`app/media.py` + `app/oss.py`） | `POST /api/ai/chat/upload` |
| 6 | 会话管理 / 历史回放 | —（`app/persistence.py`） | `GET|DELETE /api/ai/chat/sessions*`、`GET /api/ai/chat/history` |
| 7 | 长期记忆管理 | —（`app/longterm.py` + `app/memstore.py`） | `GET /api/ai/chat/memories`、`DELETE /api/ai/chat/memories/{key}` |

## 四、核心能力详解

### 4.1 AI 养宠顾问对话

#### 4.1.1 编排结构（chat_graph）

```
START → classify_intent ──┬─(knowledge / health / medical_urgent)─→ retrieve_knowledge ──┬─→ recall_memory → compose → generate → END
                          ├─(tool_query)──────────────────────────────────────────────────┤
                          └─(chitchat)────────────────────────────────────────────────────→
```

| 节点 | 职责 | 关键实现 |
|---|---|---|
| `classify_intent` | 意图识别 | 真实模式用 LLM 结构化输出 5 类意图（`chitchat` / `knowledge` / `health` / `medical_urgent` / `tool_query`）；失败降级关键词规则；**纯附件消息（无文字）直接归 `knowledge`**，不浪费一次分类调用 |
| `retrieve_knowledge` | RAG 语义检索 | 仅对 `knowledge` / `health` / `medical_urgent` 三类生效；命中相似度阈值的结果带来源注入 Prompt；链路不可用时抛 `RagUnavailable` 直接报错 |
| `tool_action` | Agent 工具调用 | 仅 `tool_query` 生效；`create_agent` 驱动 ReAct 工具调用，查询用户真实数据（6 个工具，见下） |
| `recall_memory` | 长期记忆召回 | 从 LangGraph runtime store 读取「用户级 + 当前宠物级」记忆渲染为 bullet 文本；store 不可用 / 开关关闭时降级为空 |
| `compose` | Prompt 组装 | 拼接最终 System Prompt（见 4.1.3），裁剪最近 `HISTORY_MAX_MESSAGES` 条历史；当前轮带图时组装多模态分片 |
| `generate` | 生成 | 调 LLM 流式生成并把回复写回图状态 `messages`（随 checkpoint 自动持久化）；含图时切换视觉模型客户端 |

> 顺序说明：`retrieve_knowledge` 与 `tool_action` 按意图二选一（知识类走 RAG、工具类走 Agent），两者均会串接 `recall_memory`——即**长期记忆块始终参与组装**，与知识上下文 / 工具数据可叠加注入；各块内容为空时对应模板块整体省略，Prompt 始终可完整渲染。

#### 4.1.2 Agent 工具集（`app/tools.py`）

工具按请求动态构建，**闭包捕获 `user_id`**，因此天然具备用户隔离；全部只读，失败返回空提示不影响主流程；单次返回内容截断 2000 字符控制上下文规模。

| 工具名 | 用途 | 数据来源 |
|---|---|---|
| `get_my_pets` | 我的宠物档案与健康信息 | pet-service `/pet/my-list` |
| `get_my_orders` | 我的近期订单（含自提码） | shop-service `/shop/order/page` |
| `get_my_cart` | 我的购物车 | shop-service `/shop/cart` |
| `get_product_reviews` | 指定商品的用户评论 | shop-service `/shop/review/page` |
| `get_hot_posts` | 社区当前热门动态 | space-service `/space/page?sort=hot` |
| `search_products` | 关键词搜索在售商品 | shop-service `/shop/product/page` |

#### 4.1.3 Prompt 组装与安全护栏（`app/persona.py`）

System Prompt 由「基础人设 + 五个可选上下文块 + 要求」构成，各块为空则整体省略：

| 块 | 内容来源 | 作用 |
|---|---|---|
| 宠物档案块 | 前端传入的 `pet`（名字/物种/品种/年龄 + 7 项健康字段） | 让建议与宠物个体匹配 |
| 长期记忆块 | runtime store（用户偏好 / 宠物习性 / 病史） | 跨会话个性化，回答中不暴露「记忆」实现细节 |
| 知识块 | RAG 检索结果（含来源标注） | 降低幻觉，支持引用 |
| 用户数据块 | Agent 工具查询到的真实业务数据 | 杜绝凭空猜测订单 / 购物车 / 口碑 |
| 医疗护栏块 | 命中 `medical_urgent` 时强制注入 | 明确声明「不能替代兽医诊断」，必须引导就医、禁止自行用药 |

人设定位为**中立的养宠顾问**（不扮演宠物、不使用拟人化语气），并要求「档案未提供的信息先向用户确认再作答」。

#### 4.1.4 对话记忆（双层）

| 层 | 载体 | 生命周期 | 说明 |
|---|---|---|---|
| 短期记忆（上下文） | LangGraph 官方 `PostgresSaver` checkpoint | 跟随会话 | 图状态 `messages` 按 `thread_id = petverse-chat:{userId}:{sessionId}` 在每个超级步自动落库；`compose` 时只取最近 `HISTORY_MAX_MESSAGES`（默认 40）条控制 token 成本 |
| 长期记忆（跨会话） | LangGraph 官方 `PostgresStore` runtime store | 跨会话、与删除会话解耦 | 命名空间两级隔离：`petverse/user_mem/{userId}` 与 `petverse/pet_mem/{userId}/{petId}`；每轮正常结束后后台抽取（见 4.1.5） |

**中断补写机制**（解决「用户点停止后 AI 失忆」）：用户中止生成时，取消路径在 `asyncio.shield` 保护下把「用户问题 + 已产出的部分回复（标记 `interrupted`）」经 `graph.aupdate_state(as_node="generate")` 补写回图状态——图状态就此收尾（`next` 为空），下一轮从 START 重新展开，不会重放未完成节点；同时兜底写入 PostgreSQL 供前端历史回放（`save_turn_if_exists` 在会话已被删除时原子跳过，不复活历史）。用户消息无条件落库，即使中止时零产出，首轮提问也不会丢。

**存量会话回填**：升级前仅有历史消息、checkpoint 中无记录的会话，首次对话时用 PG 最近 N 条历史回填一次；回填消息 id 为确定性值（`bf-{序号}-{时间戳}`），`add_messages` 按 id 覆盖，重复触发不产生重复消息。

#### 4.1.5 长期记忆抽取（`app/longterm.py`）

每轮正常结束后（真实模式 + 回复非空）由后台任务异步执行，不阻塞 `done` 事件：

1. 读取现有记忆清单（用户级 + 宠物级各限 20 条）作为比对依据；
2. LLM 结构化输出 `MemoryUpdateResult`（操作列表）；
3. 逐条应用：`add` 用新 uuid key、`update` 复用原 key 并保留 `createdAt`、`delete` 按 key 删除。

抽取原则（写进 System Prompt）：只记录**长期有效**的养宠信息（用户偏好、宠物习性、病史过敏等），忽略一次性细节与寒暄；重复或演进的信息必须输出 `update` 而非 `add`（**防抖机制**）；无有效信息时输出 `none`。安全阀：单轮操作数上限 8 条、单条内容 50 字截断。中断轮（partial 回复）不触发抽取——不完整信息易产出误导性记忆。

前端可查看与删除长期记忆（`GET /ai/chat/memories`、`DELETE /ai/chat/memories/{key}`），删除接口按「用户 ID + scope + petId」定位命名空间，**天然隔离越权**（他人无法猜 key 删除别人的记忆）。

#### 4.1.6 SSE 流式协议与并发控制（`app/chat.py`）

**事件协议**：

| 事件 | 载荷 | 时机 |
|---|---|---|
| `meta` | `{sessionId}` | 首帧，前端据此绑定自动新建的会话并刷新列表 |
| `delta` | `{content}` | 生成过程中的文本增量 |
| `done` | — | 正常结束（此时两侧存储均已写入） |
| `error` | `{msg}` | 异常 / 过载 / 并发超限，发出后结束流 |

SSE 响应头固定 `Cache-Control: no-cache`、`X-Accel-Buffering: no`（禁 Nginx 反代缓冲）、`Connection: keep-alive`；超过 15s 无 token 产出即发送注释行 `: ping` 心跳防断连。

**并发与限流**：

| 机制 | 参数 | 说明 |
|---|---|---|
| 全局并发闸 | 20 路，3s 获取超时 | 超时返回过载提示，保护 LLM 调用与信号量池 |
| 单用户并发闸 | 2 路 | 额度计数存于 Redis（`ai:conc:user:{userId}`，Lua 原子占用/归还，TTL 5 分钟自愈），多实例部署时跨实例生效；建会话前做只读预检查（避免超限请求白建空会话），正式占用在流生成器内完成、`finally` 归还 |
| 消息长度 | 2000 字符 | 超长直接拒绝，保护上下文窗口与 token 成本 |
| 附件数量 | 4 个 / 轮 | 可经 `.env` 调整 |
| 首帧前重试 | 最多 2 次尝试 | 仅在**未产出任何内容**时静默重建流重试；已产出后失败不重试，避免重复输出 |

**会话模型**：按「用户 + 宠物 + 会话」三级隔离——一只宠物可有多个会话；切换宠物 / 新建对话进入待创建态，**会话在用户发出首条消息时才落库**（避免频繁切换堆积空会话）；首轮消息自动作为会话标题（仍为「新会话」时刷新）；侧栏跨宠物统一展示全部历史会话。

### 4.2 宠物健康智能评估（health_graph）

```
START → load_profile → analyze → persist → END
```

- **load_profile**：整理宠物档案与 7 项健康字段为文本，标记缺失项（物种、年龄、疫苗、驱虫等）；
- **analyze**：LLM 结构化输出 `PetHealthReport`——综合评分（0-100）、评级（excellent / good / fair / warning）、一句话总结、风险点、改进建议、近期养护计划、疫苗/驱虫/体检提醒（含紧急程度）、免责声明。评分标准显式写进 Prompt（信息完整且正常 85-100，有风险项依次下探），并明确「不得做医疗诊断，仅基于用户填写信息评估」；
- **persist**：报告落 `pet_health_report` 表（同库历史留存）。

**降级**：LLM 失败或 mock 模式走规则评估（`_rule_report`）——按档案缺失项、病史关键词、特殊时期、疫苗/驱虫记录缺项扣分，产出同样结构完整的可演示报告。`GET /ai/health/history?petId=` 可查询某宠物历史评估（时间倒序）。

### 4.3 商品评论智能摘要（review_graph）

```
START → fetch_reviews → summarize → persist_cache → END
```

- 拉取商品评论（最多 50 条，参与摘要 40 条、单条截断 300 字控制上下文）；
- LLM 结构化输出 `ReviewSummary`：情感倾向、一句话总结、优点、缺点、3-6 个高频关键词；Prompt 强制「优缺点必须来自评论内容，不要编造」；
- 结果写两级缓存（TTL 6 小时，有新增评论自然过期重建），无评论时返回明确占位摘要且**不写缓存**；
- **降级**：LLM 失败时用规则摘要——按评分均值定情感、情感词表挑代表性短句作优缺点、中文 n-gram（2-4 字）词频提关键词。

### 4.4 个性化推荐流（recommend_graph）

```
START → gather_profile → recall_candidates → rerank → persist_cache → END
```

- **gather_profile**：`asyncio.gather` 并发拉取五路画像（宠物 / 订单 / 购物车 / 评价 / 我的动态），任一失败降级为空不影响其余；
- **recall_candidates**：从画像提取关键词（宠物物种品种 + 交互过的商品名），关键词搜商品 + 泛化在售商品 + 热门动态，去重规范化后作为候选（商品 ≤16 + 动态 ≤8）；
- **rerank**：LLM 结合画像压缩文本重排，产出至多 8 条「内容 + 推荐理由（≤30 字）」；**防幻觉约束**：只能用候选列表中出现过的 id，模型编造的 id 在合并阶段被过滤；重排结果为空或失败时降级启发式排序（商品优先、动态按点赞数）；
- **persist_cache**：按 `用户 + 场景` 写两级缓存（TTL 10 分钟）；`refresh=true` 跳过缓存强制刷新。
- **单飞防击穿**：缓存未命中时同一 key 只放行一次真实计算（五路画像 + 召回 + LLM 重排），其余并发请求等锁后直接取结果；写入 TTL 附加 0~10% 随机抖动，避免同批缓存同时过期。

### 4.5 多模态附件（`app/media.py` / `app/oss.py`）

| 类型 | 处理方式 | 大小上限 |
|---|---|---|
| 图片 | 当前轮以 `image_url` 分片直发视觉模型（如百炼 qwen-vl） | 10 MB |
| 音频 | 经 OpenAI 兼容 ASR（如 paraformer-v2）转写为文本，并优先作为意图识别 / RAG 检索的信号 | 20 MB |
| 视频 | 无法直接理解，生成明确的文字引导（建议截取关键画面以图片发送） | 50 MB |

- **上传**：`POST /ai/chat/upload` 直传 OSS，对象 key 为 `ai-chat/{userId}/{yyyyMMdd}/{uuid}.ext`，URL 由浏览器回放与视觉模型回源拉取共用，**不占本服务带宽**；类型按 MIME 主类型判定、扩展名兜底，大小边读边累计、超限立即中断不产生上传流量；
- **降级**：视觉模型未配置时图片降级为文字占位提示（引导用户文字描述）；ASR 未配置或转写失败时同样降级为提示，绝不阻断对话主流程；
- **安全**：服务端读取附件字节（转写用）只接受**本人命名空间**的对象 key，且只按 key 取值、不按用户提交的 URL 抓取（防 SSRF）；转写结果回填 `transcript` 字段，消息通道中只保存文本描述（避免图片分片进 checkpoint 撑爆记忆存储）。

## 五、接口清单

> 以下均为经网关的对外路径（网关 `StripPrefix=1`）；除上传接口外，所有接口需携带 JWT，网关注入 `X-User-Id`，缺失 / 非法统一返回 `{"code":401,"msg":"未登录"}`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/ai/chat/stream` | SSE 流式对话，体 `{"message","pet":{...},"sessionId","attachments":[...]}`；首帧 `meta` 回传 sessionId |
| POST | `/api/ai/chat/upload` | 上传多模态附件（multipart），返回 attachment 信息供前端原样放入 `attachments` |
| GET | `/api/ai/chat/history?sessionId=` | 会话历史（时间正序，含 `interrupted` 标记与附件） |
| GET | `/api/ai/chat/sessions` | 用户全部会话（跨宠物，最近活跃在前，每条带 petId） |
| DELETE | `/api/ai/chat/sessions/{id}` | 删除会话（连带消息与 checkpoint 记忆） |
| GET | `/api/ai/chat/memories` | 用户全部长期记忆（用户级 + 所有宠物级，最近更新在前） |
| DELETE | `/api/ai/chat/memories/{key}?scope=&petId=` | 删除单条长期记忆 |
| POST | `/api/ai/health/assess` | 宠物健康评估，体为宠物档案（含 `health`），返回结构化报告 |
| GET | `/api/ai/health/history?petId=` | 宠物历史健康评估（时间倒序） |
| POST | `/api/ai/shop/review/summary` | 商品评论摘要，体 `{"productId"}` |
| GET | `/api/ai/recommend/feed?scene=home\|shop&refresh=` | 个性化推荐流（`refresh=true` 跳过缓存） |

**SSE 事件序列**：`meta` → `delta * n` → `done`；异常时 `error` 后结束流。业务错误（参数 / 会话不存在 / 过载）以 `{code, msg, data}` JSON 报文返回，HTTP 状态码恒为 200。

## 六、数据存储

全部业务数据落在 PostgreSQL 库 `petverse_ai`（与 Java 服务的 MySQL 体系独立）：

| 存储 | 表 / 结构 | 用途 | 建表方式 |
|---|---|---|---|
| 会话管理 | `chat_session` | 会话（user_id + pet_id 隔离，标题、活跃时间） | 启动时幂等自动创建 |
| 消息历史 | `chat_message` | 前端历史回放（`interrupted` 标记、`attachments` JSONB） | 启动时幂等自动创建 |
| 短期记忆 | LangGraph checkpoint 表 | LLM 上下文记忆（图状态持久化） | `PostgresSaver.setup()` 幂等创建 |
| 长期记忆 | LangGraph store 表 | 跨会话用户 / 宠物偏好与事实 | `PostgresStore.setup()` 幂等创建 |
| 知识库 | pgvector 集合 `petverse_kb` | RAG 向量检索 | 导入脚本重建 |
| 健康报告 | `pet_health_report` | 历史评估报告（含 payload JSONB） | `db/schema_pgvector.sql` |
| 结果缓存温层 | `ai_cache` | Redis 故障时的缓存兜底（UPSERT + 过期时间） | `db/schema_pgvector.sql` |
| 结果缓存热层 | Redis db=3 | 评论摘要 / 推荐结果 | 运行时写入 |
| 并发额度计数 | Redis db=3 `ai:conc:user:{userId}` | 单用户并发额度（多实例共享，TTL 5 分钟兜底回收） | 运行时写入 |

> 服务启动时 `lifespan` 依次初始化 Redis、PG 连接池、checkpoint、store、Nacos 注册；停机时反注册并释放各连接池。存量数据迁移（MySQL → PostgreSQL）用 `scripts/migrate_mysql_to_pg.py`（一次性、幂等、保留原会话 ID）。

## 七、配置项（`.env`）

| 配置组 | 关键项 | 说明 |
|---|---|---|
| 服务与注册 | `AI_SERVICE_NAME/IP/PORT`、`NACOS_SERVER_ADDR` | 默认 127.0.0.1:8086，注册名 `ai-service` |
| 对话模型 | `LLM_API_KEY/BASE_URL/MODEL` | OpenAI 兼容；留空或 `MOCK_CHAT=true` 进入 mock 模式 |
| 视觉模型 | `LLM_VISION_MODEL/API_KEY/BASE_URL` | Key / BaseURL 缺省时自动回落 `LLM_*`；模型未配置时图片走文字占位 |
| 语音转写 | `ASR_API_KEY/BASE_URL/MODEL` | 三项齐全才启用，否则音频降级为文字提示 |
| Embedding | `EMBEDDING_API_KEY/BASE_URL/MODEL/DIM` | 不配置则知识检索不可用（知识类提问明确报错）；`DIM` 必须与 pgvector 维度一致 |
| RAG | `RAG_ENABLED / RAG_TOP_K / RAG_SCORE_THRESHOLD / RAG_COLLECTION` | 默认开关开、召回 4 条、阈值 0.35、集合 `petverse_kb` |
| 记忆 | `MEMORY_ENABLED`、`MEMORY_MAX_ITEMS`、`HISTORY_MAX_MESSAGES` | 长期记忆总开关、注入条数上限 20、上下文窗口 40 条 |
| PostgreSQL | `PG_HOST/PORT/USER/PASSWORD/DB`、`PG_POOL_MIN/MAX` | 库 `petverse_ai` |
| Redis | `REDIS_HOST/PORT/PASSWORD/DB` | db=3 为 AI 结果缓存专用 |
| OSS | `OSS_ENDPOINT/ACCESS_KEY_ID/ACCESS_KEY_SECRET/BUCKET_NAME/DOMAIN` | 与 Java 侧共用同一个桶；缺任一项上传接口明确报错 |
| 附件 | `CHAT_MAX_ATTACHMENTS`、`MEDIA_MAX_IMAGE_MB/AUDIO_MB/VIDEO_MB` | 4 个 / 10MB / 20MB / 50MB |
| 业务服务 | `PET/SHOP/SPACE/USER/SOCIAL/REMARK_SERVICE_URL`、`HTTP_TIMEOUT` | 直连拉取上下文，失败降级为空 |
| 缓存 TTL | `REVIEW_SUMMARY_TTL`、`RECOMMEND_CACHE_TTL` | 6 小时 / 10 分钟 |
| 生成参数 | `LLM_MAX_TOKENS`、`LLM_TEMPERATURE` | 默认 512 / 0.8 |

## 八、容错与降级策略汇总

设计原则：**按业务语义分级**——记忆与上下文类能力「宁可降级不可中断」，会话管理与知识检索类能力「宁可失败不可静默」。

| 环节 | 故障场景 | 策略 |
|---|---|---|
| 对话记忆（checkpoint） | PG 不可用 | 读降级为「无历史」、写静默失败；建表失败 30s 冷却重试，PG 恢复后无需重启自动启用 |
| 长期记忆（store） | PG 不可用 | 读降级为空、写静默；同上冷却重试自愈 |
| 会话管理 | PG 不可用 / 会话不存在 | **明确抛错**转业务报文（对话前提不允许静默降级） |
| 消息持久化 | PG 写入失败 | 仅记日志，不影响本次对话 |
| RAG 检索 | Embedding 未配置 / 向量库异常 | **抛 `RagUnavailable` 明确报错**，不降级关键词检索 |
| 意图识别 | LLM 失败 / mock | 降级关键词规则（含医疗急症词表） |
| 工具调用 | 业务服务异常 | 跳过真实数据上下文，对话继续 |
| 健康评估 | LLM 失败 / mock | 降级规则评分报告 |
| 评论摘要 | LLM 失败 / mock / 无评论 | 降级规则摘要；无评论返回占位且不写缓存 |
| 推荐 | 画像拉取失败 / LLM 失败 | 画像缺失退化通用结果；重排失败降级启发式排序 |
| 结果缓存 | Redis 故障 | 读自动回落 `ai_cache` 温层，写仅落温层；两级都不可用才重新计算 |
| 并发额度计数 | Redis 故障 | 降级为进程内计数（单实例上限仍生效）；按占用时的计数方式归还，两套计数不错位 |
| 缓存击穿 / 雪崩 | 热点 key 过期瞬间并发未命中 | 同 key 单飞只放行一次计算；写入 TTL 加随机抖动错开过期时间 |
| 音频转写 | ASR 未配置 / 失败 | 降级为文字占位提示，不阻断对话 |
| 图片理解 | 视觉模型未配置 | 降级为文字占位提示，引导用户文字描述 |
| 附件上传 | OSS 未配置 / 上传失败 | **明确报错**（不静默降级，避免附件悄悄丢失）；上传内部含 1 次抖动重试 |
| LLM 流式输出 | 首帧前失败 / 中途异常 | 首帧前静默重建重试一轮；已产出内容后失败发 `error` 事件收尾 |
| 前端连接 | 网络闪断 / 链路静默 | 心跳注释行保活；前端侧配连接看门狗（10s）与流看门狗（25s）、失败气泡可一键重发 |
| Nacos 注册 | SDK 失败 / 实例丢失 | OpenAPI 兜底注册；自建 5s 心跳线程，检测到实例丢失自动重注册（30s 节流） |
| 无 Key 联调 | 未配置 LLM Key | mock 模式：编排逻辑照常执行，回复由内置语料按打字机节奏输出，跨轮记忆一致 |

## 九、知识库语料与导入

- 语料位于 `app/knowledge/*.md`：`vaccine.md`（疫苗）、`deworming.md`（驱虫）、`feeding.md`（喂养）、`disease.md`（常见病）、`behavior.md`（行为护理）；
- 导入：`.venv\Scripts\python -m scripts.ingest_knowledge` —— 按中文标点递归切分（chunk 280 / overlap 40），**先清空集合再全量写入**（幂等，可反复执行，以文件为唯一事实来源）；
- Embedding 未配置或向量库不可用时脚本报错退出（RAG 只有 pgvector 一条链路，不做关键词降级）。

## 十、本地运行与联调

```powershell
cd ai-service
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # 留空 LLM_API_KEY 即进入 mock 模式，可无 Key 全流程联调
psql -U postgres -d petverse_ai -f db/schema_pgvector.sql   # 库需先 CREATE DATABASE petverse_ai
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8086
```

- **最小依赖**：无 Nacos / Redis / PG 也可启动，各能力按上表自动降级（会话管理与 RAG 除外，会明确报错）；
- **mock 模式**：`MOCK_CHAT=true` 或未配置 `LLM_API_KEY`，无需任何外部模型服务即可演示对话、记忆、会话管理全链路；
- **切换服务商**：改 `.env` 中 `LLM_BASE_URL` + `LLM_MODEL` 即可（如 DeepSeek `https://api.deepseek.com/v1` + `deepseek-chat`，百炼 `https://dashscope.aliyuncs.com/compatible-mode/v1` + `qwen-plus`），代码零改动。
