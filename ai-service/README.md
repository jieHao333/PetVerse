# PetVerse AI 服务

基于 FastAPI 的 AI 能力服务，监听 8086，启动后以 `ai-service` 注册到 Nacos，由网关按 `/api/ai/**` 转发。调用业务服务时同样从 Nacos 取健康实例并轮询。

## 能力

| 能力 | 说明 | 代码位置 |
|---|---|---|
| AI 养宠顾问 | 自主 AgentLoop：模型自行决定检索 / 查数 / 作答，SSE 流式，多会话 | `app/agent/` |
| 健康智能评估 | 根据宠物档案与健康信息生成评分、风险、养护计划、提醒 | `app/graph/health_graph.py` |
| 商品评论摘要 | 汇总真实评论生成情感 / 优缺点 / 关键词 | `app/graph/review_graph.py` |
| 个性化推荐 | 结合宠物画像、订单、购物车、评价、动态重排 | `app/graph/recommend_graph.py` |
| MCP / Skill 扩展 | 管理员配置远程 MCP 工具与技能包，热装配进对话 Agent | `app/ext_api.py`、`app/agent/extensions.py` |
| 模型配置与热切换 | 管理员维护「供应商 + 模型」，支持 OpenAI 兼容与 Anthropic 两种协议，切换后下一轮对话生效 | `app/model_registry.py`、`app/ext_api.py` |

## 目录结构

```text
ai-service/
├── Dockerfile
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py            # FastAPI 入口、Nacos 注册
│   ├── config.py          # Settings（全部环境变量）
│   ├── agent/             # 对话 AgentLoop、工具、扩展装配、幂等
│   ├── graph/             # 健康 / 评论 / 推荐编排图
│   ├── kb/                # 切分、解析、向量 + BM25 混合检索
│   ├── knowledge/         # 内置语料（21 md + 1 pdf + 1 docx）
│   ├── model_registry.py  # 当前生效模型解析（供应商 + 模型，支持 OpenAI / Anthropic 协议）
│   └── skill_store.py     # Skill 技能包读写
├── db/schema_pgvector.sql
├── scripts/               # 知识库导入、RAG 评测、MySQL → PG 迁移
├── skills/petcare-calc/   # 示例技能
├── mcp_servers/           # 示例 MCP 服务
└── tests/
```

## 运行

### Docker

```bash
docker build -t petverse-ai:latest .

docker run -d --name petverse-ai -p 8086:8086 \
  --env-file .env \
  -e AI_SERVICE_HOST=0.0.0.0 \
  -e PG_HOST=host.docker.internal -e PG_USER=petverse -e PG_PASSWORD=petverse123 \
  -e REDIS_HOST=host.docker.internal -e REDIS_PASSWORD=123456 \
  -e NACOS_SERVER_ADDR=host.docker.internal:8848 \
  -e INTERNAL_TOKEN="与业务服务一致" \
  petverse-ai:latest
```

`--env-file` 的值会被 `-e` 覆盖，后者优先级更高。单独 `docker run` 时容器 IP 对宿主机上的调用方不可路由，注册发现链路请用后端根目录的 `docker-compose.micro.yml`（同一网络内按服务名互访，`PG_*`、`REDIS_*`、`NACOS_SERVER_ADDR` 已指向容器地址）。

### 本地

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows；macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # 按需填写 LLM / Embedding / PostgreSQL / Redis

python -m scripts.ingest_knowledge        # 首次导入语料
python -m uvicorn app.main:app --host 127.0.0.1 --port 8086
```

服务发现单元测试（无需 Nacos）：

```bash
python -m unittest discover -s tests -v
```

> `--host` 决定监听地址，`AI_SERVICE_IP` 决定注册进 Nacos 的地址。只绑 `127.0.0.1` 时其他机器连不上；服务器或多实例请用 `0.0.0.0` 并让 `AI_SERVICE_IP` 留空自动选网卡。

## 配置

复制 `.env.example` 为 `.env`。Embedding 为 OpenAI 兼容格式；对话模型优先读管理端配置，`.env` 只作兜底。

### 模型

对话模型不再靠改代码或重启切换：管理员在「AI 扩展 → 模型」页维护**供应商（协议 + Base URL + Key）
\+ 模型清单**，选中哪个下一轮对话就用哪个（见 `app/model_registry.py`）。

- **两种协议**：`openai` 走 `/chat/completions`（地址一般带 `/v1`，覆盖 DeepSeek、百炼兼容模式、
  vLLM、Ollama 等）；`anthropic` 走 `/v1/messages`（地址不带 `/v1`，覆盖 Claude 官方与各类
  Anthropic 兼容网关）。填错 `/v1` 时后端会自动纠正，不需要人工试错。
- **一个供应商挂多个模型**：同一把 Key 下的快模型 / 旗舰模型共用供应商配置，切换只改一个指针。
- **热切换**：切换后进程内缓存立即过期，无需重启；正在进行的流式回答继续用旧模型跑完。
  多 worker 部署时其他进程靠 60s TTL 最终一致（默认单 worker 启动，不涉及）。
- **解析顺序**：数据库里启用且选中的模型 → `.env` 的 `LLM_*`。后者保证「没配过」与
  「PostgreSQL 不可用」时对话仍然可用，管理页会明确提示当前是否回落。
- **凭证**：API Key 明文存于 PostgreSQL（与 `.env` 同级信任域，请在数据库侧做好访问控制与备份加密）；
  管理接口只回显尾号（`sk-…a8a9`），编辑时留空表示沿用原 Key。

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | **兜底**对话模型。DeepSeek：`https://api.deepseek.com/v1` + `deepseek-chat`；百炼：`https://dashscope.aliyuncs.com/compatible-mode/v1` + `qwen-plus` |
| `MOCK_CHAT` | `true` 时强制走内置模拟回复，忽略管理端与 `.env` 的所有模型配置 |
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | 向量化，知识检索必需。`EMBEDDING_DIM` 必须与 `kb_chunk.embedding` 维度一致，默认 1024 |
| `LLM_VISION_MODEL` / `LLM_VISION_API_KEY` / `LLM_VISION_BASE_URL` | 视觉**兜底**：仅在对话模型回落到 `.env` 时生效；管理端选中模型时以模型行上的「支持图片」勾选为准，没勾就让图片降级为文字占位 |
| `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` | 生成参数，默认 `131072`（128K）/ `0.8`；它限的是**单次生成**的输出长度，不是上下文窗口。模型行上的「最大输出」优先于它（管理页可填 `128K` / `1M` 或直接填 token 数） |

对话附件只支持图片（jpg / png / webp / gif / bmp，单张 ≤ `MEDIA_MAX_IMAGE_MB`）；音频与视频已下线，
上传接口会直接拒绝。Embedding 不会复用 LLM 的 Key（DeepSeek 无 embedding 接口），未配置时知识类提问
返回 `RagUnavailable`，不做关键词降级。

### 存储

| 变量 | 默认 | 说明 |
|---|---|---|
| `PG_HOST` / `PG_PORT` / `PG_USER` / `PG_PASSWORD` / `PG_DB` | `localhost` / `5432` / `postgres` / `123456` / `petverse_ai` | 会话消息、checkpoint 记忆、知识库、健康报告、缓存 |
| `PG_POOL_MIN` / `PG_POOL_MAX` | `1` / `10` | 连接池 |
| `PG_AUTO_CREATE_TABLES` | `true` | 启动时幂等补建 checkpoint 等表 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | `localhost` / `6379` / 空 / `1` | 结果缓存与并发计数 |
| `OSS_ENDPOINT` / `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_BUCKET_NAME` / `OSS_DOMAIN` | `oss-cn-beijing.aliyuncs.com` / 空 | 对话附件持久化，与 Java 侧共用一个桶；缺项时上传接口报错 |

### 服务发现

| 变量 | 默认 | 说明 |
|---|---|---|
| `AI_SERVICE_NAME` | `ai-service` | 注册名，改名需同步网关 `lb://ai-service` 路由 |
| `AI_SERVICE_HOST` / `AI_SERVICE_PORT` | `127.0.0.1` / `8086` | 监听地址与端口 |
| `AI_SERVICE_IP` | 空 | 注册到 Nacos 的 IP，留空自动探测并回连自检 |
| `NACOS_SERVER_ADDR` / `NACOS_GROUP` / `NACOS_NAMESPACE` | `localhost:8848` / `DEFAULT_GROUP` / 空 | 须与 Java 侧一致 |
| `INTERNAL_TOKEN` | 空 | 随下游请求以 `X-Internal-Token` 发出，须与 Java 侧同名变量一致 |
| `SERVICE_DISCOVERY_CACHE_TTL` / `HTTP_TIMEOUT` | `10` / `8` | 实例列表缓存秒数 / 业务调用超时 |

各业务服务名默认与 Java 的 `spring.application.name` 一致，改名需与 Nacos 注册名同步：

```dotenv
PET_SERVICE_NAME=pet-service
SPACE_SERVICE_NAME=space-service
USER_SERVICE_NAME=user-service
SOCIAL_SERVICE_NAME=social-service
REMARK_SERVICE_NAME=remark-service
SHOP_SERVICE_NAME=shop-service
DATA_SERVICE_NAME=data-service
```

多机部署时，每个实例必须注册成消费方可连且互不相同的 `(ip, port)`：

| 位置 | 配置 |
|---|---|
| 全部 ai-service 实例 | `AI_SERVICE_HOST=0.0.0.0`、`AI_SERVICE_IP=`（留空） |
| 全部 ai-service 实例 | `NACOS_SERVER_ADDR=<Nacos 内网 IP>:8848` |
| 全部 ai-service 实例 | `INTERNAL_TOKEN` 与各 Java 服务同名变量完全一致 |

服务间身份以 `X-Internal-Token` 为准，不要依赖来源 IP：跨机部署时源地址不是回环，会被剥离身份头，表现为推荐「有时个性化、有时全是热门」。

### Agent 与 RAG

| 变量 | 默认 | 说明 |
|---|---|---|
| `RAG_ENABLED` / `RAG_TOP_K` / `RAG_RECALL_K` | `true` / `5` / `20` | 开关 / 注入片段数 / 单通道召回数 |
| `RAG_MIN_SIM` / `RAG_LEX_MIN_TERMS` / `RAG_FUSION_ALPHA` | `0.30` / `2` / `0.85` | 向量门槛 / 关键词门槛 / 向量通道融合权重 |
| `RAG_SPECIES_STRICT` / `RAG_SPECIES_PENALTY` | `false` / `0.5` | 物种不符时剔除或降权 |
| `RAG_QUERY_REWRITE` / `RAG_REWRITE_MIN_CHARS` | `true` / `12` | 多轮追问改写开关 / 跳过阈值 |
| `KNOWLEDGE_DIR` / `KB_CHUNK_SIZE` / `KB_CHUNK_MIN` / `KB_CHUNK_MAX` / `EMBED_BATCH_SIZE` | 空 / `600` / `80` / `900` / `10` | 语料目录与切分参数 |
| `KB_STORAGE_DIR` / `KB_MAX_UPLOAD_MB` / `KB_ADMIN_TOKEN` | 空 / `20` / 空 | 上传原文留存目录 / 大小上限 / 直连运维令牌 |
| `SKILLS_DIR` | `skills/` | 技能包根目录 |
| `AGENT_MAX_STEPS` | `3` | 单轮最多「模型 → 工具」循环次数，超限强制收敛 |
| `AGENT_TOOL_GROUPS` | `knowledge,skill,pet,shop,cart,community,interaction,pet_edit,publish,profile` | 启用分组；`content`（评论）默认关闭；`skill` 始终启用 |
| `AGENT_APPROVAL_TOOLS` | `publish_post` | 需人工审批的工具，依赖 checkpoint |
| `AGENT_MCP_ENABLED` / `MCP_LOAD_TIMEOUT` / `MCP_CACHE_TTL` | `true` / `10` / `60` | MCP 装配开关、连接超时、缓存 |
| `HISTORY_MAX_MESSAGES` | `40` | 注入上下文的最近消息条数 |
| `MEMORY_ENABLED` / `MEMORY_MAX_ITEMS` | `true` / `20` | 长期记忆开关 / 注入条数 |
| `REVIEW_SUMMARY_TTL` / `RECOMMEND_CACHE_TTL` | `21600` / `300` | 评论摘要 / 推荐缓存 TTL（秒） |

## 知识库

### 初始化

```bash
psql -U postgres -c "CREATE DATABASE petverse_ai;"
psql -U postgres -d petverse_ai -f db/schema_pgvector.sql
```

脚本启用 `vector` 扩展并创建 16 张表：

- 业务表：`chat_session`、`chat_message`、`pet_health_report`、`ai_cache`；
- 知识库：`kb_document`、`kb_chunk`、`kb_meta`；
- 扩展配置：`ai_mcp_server`、`ai_model_provider`、`ai_model_setting`；
- LangGraph 记忆：`checkpoints` / `checkpoint_blobs` / `checkpoint_writes` / `checkpoint_migrations`、`store` / `store_migrations`。

脚本幂等，可反复执行；所有业务表服务启动时也会自动补建，手工执行只用于首次初始化或结构审阅。LangGraph 的迁移版本表**不要**手工写入版本号——留空，库升级后 `setup()` 会自己补跑缺失迁移。Skill 不落库，是磁盘上的 `skills/<name>/SKILL.md`。

### 导入

语料目录 `app/knowledge/`，支持 md / pdf / docx / doc，递归扫描：

- md：文件头 `---` 元数据块可声明 `title` / `category` / `species` / `tags`，按标题层级切分。
- docx：按段落样式还原层级，表格转「表头：值」文本。
- pdf：PyMuPDF 按页抽取，页码作为小节路径。
- doc：调用系统 `antiword` / `catdoc`，缺失时明确报错。

```bash
python -m scripts.ingest_knowledge            # 增量，sha256 未变的文档跳过
python -m scripts.ingest_knowledge --full     # 清空后全量重建
python -m scripts.ingest_knowledge --force    # 不跳过，全部重新向量化
python -m scripts.ingest_knowledge --file x.pdf
python -m scripts.ingest_knowledge --stats    # 规模与文档清单
```

按 `doc_id`（文件名主干）覆盖写入，单事务「删旧片段 → 写文档行 → 写片段」，并自增 `kb_meta.version` 触发 BM25 索引惰性重建。单文件失败只进失败清单，脚本以非零码退出。

### 评测

```bash
python -m scripts.eval_rag --validate   # 校验黄金集期望子串存在
python -m scripts.eval_rag              # 三模式消融对比
```

当前语料 23 篇（约 6.4 万字 / 174 片段）。按 `scripts/eval_rag.py` 的 83 条黄金查询，混合检索 hit@1 92.8%、hit@3 97.6%、MRR 0.948；同语料纯向量为 91.6% / 0.941。更换语料或切分参数后需重新评测。

## 接口

| 方法 | 路径（经网关后） | 说明 |
|---|---|---|
| POST | `/api/ai/chat/stream` | SSE 流式对话；体 `{"message","pet":{...},"sessionId"}` |
| POST | `/api/ai/chat/approve` | 审批敏感操作并继续；体 `{"sessionId","approvalId","approved","reason"}` |
| POST | `/api/ai/chat/upload` | 上传对话图片附件（仅图片），返回随消息回传的附件描述 |
| GET | `/api/ai/chat/history?sessionId=` | 会话历史，时间正序，助手消息带 `refs` |
| GET | `/api/ai/chat/sessions` | 用户全部会话，最近活跃在前 |
| DELETE | `/api/ai/chat/sessions/{id}` | 删除会话（连带消息与 checkpoint） |
| GET | `/api/ai/chat/memories` | 查看长期记忆 |
| DELETE | `/api/ai/chat/memories/{key}` | 删除单条长期记忆 |
| GET | `/api/ai/kb/documents?page=&size=&keyword=` | 知识库文档列表（管理员） |
| GET | `/api/ai/kb/documents/{docId}/preview` | 文档预览（管理员） |
| GET | `/api/ai/kb/documents/{docId}/file?inline=` | 回读原文（管理员） |
| POST | `/api/ai/kb/documents` | 上传文档入库（multipart，管理员） |
| DELETE | `/api/ai/kb/documents/{docId}` | 删除文档（管理员） |
| POST | `/api/ai/kb/reindex` | 用仓库语料全量重建（管理员，同时仅允许一个） |
| GET/POST | `/api/ai/admin/mcp/servers` | MCP 服务列表 / 新增（管理员） |
| PUT/DELETE | `/api/ai/admin/mcp/servers/{id}` | 更新 / 删除（管理员） |
| POST | `/api/ai/admin/mcp/servers/{id}/test` | 连通性测试并列出工具（管理员） |
| GET/POST | `/api/ai/admin/skills` | 技能列表 / 新增（管理员） |
| PUT/DELETE | `/api/ai/admin/skills/{name}` | 更新 / 删除技能（管理员） |
| GET | `/api/ai/admin/models` | 模型供应商列表 + 当前生效模型 + 运行时实际配置（管理员） |
| POST | `/api/ai/admin/models` | 新增模型供应商（协议 / Base URL / Key / 模型清单，管理员） |
| PUT/DELETE | `/api/ai/admin/models/{id}` | 更新 / 删除模型供应商（管理员） |
| POST | `/api/ai/admin/models/activate` | 切换当前生效模型，体 `{"providerId","model"}`（管理员） |
| POST | `/api/ai/admin/models/test` | 对指定模型发一次最小请求验证连通性（管理员） |
| POST | `/api/ai/health/assess` | 健康评估 |
| GET | `/api/ai/health/history?petId=` | 历史评估 |
| POST | `/api/ai/shop/review/summary` | 商品评论摘要，体 `{"productId"}` |
| GET | `/api/ai/recommend/feed?scene=home`（或 `scene=shop`） | 个性化推荐流，`refresh=true` 跳过缓存 |

SSE 事件顺序：`meta → refs（有命中时）→ delta × n → done`；命中审批时在 `delta` 后下发 `approval` 并结束本次流（无 `done`）；异常下发 `error`。服务端每 15s 发心跳注释行。

`/kb/*` 与 `/admin/*` 凭 `X-User-Role=ADMIN` 放行，非管理员返回 403。`KB_ADMIN_TOKEN` + 请求头 `X-Kb-Admin` 是绕过网关直连运维时的备选凭证。

## 关键机制

- **AgentLoop**：`prepare → agent ⇄ act → finalize`。`prepare` 只做确定性上下文工程（宠物档案、长期记忆、医疗护栏、系统提示）；`act` 用 `ToolNode` 并行执行工具；`AGENT_MAX_STEPS` 到顶后走 `conclude` 强制收敛。新增工具只需写 `@tool` 并在 `app/agent/tools/registry.py` 登记。
- **工具边界**：只读工具 + 可撤销写操作（购物车、点赞、登记宠物、更新健康）+ 发布动态（需审批）。下单、支付、取消订单不开放；删除类操作不开放。商品只返回在售且有库存的记录，并裁掉内部字段。
- **人工审批**：`publish_post` 在工具内调用 `interrupt()`，图在写库前挂起，前端确认后经 `Command(resume=...)` 从 checkpoint 继续；无 checkpointer 时直接拒绝执行。
- **记忆**：短期用 `PostgresSaver`，`thread_id = 用户:会话`；停止生成时用 `aupdate_state` 把问题与已产出内容（标记 `interrupted`）写回图状态。长期记忆用 runtime store，按用户 / 宠物命名空间隔离，每轮后台抽取偏好与病史。
- **混合检索**：pgvector 余弦召回 + jieba BM25，融合后按物种降权 / 过滤，双门槛保留；多轮追问先改写为自包含问句。
- **模型热切换**：`app/model_registry.py` 按「数据库生效模型 → `.env` 兜底」解析配置，并按配置签名缓存客户端；管理页切换后让缓存过期，下一轮对话自动重建（无需重启）。协议不同时结构化输出的降级顺序也随之切换——Anthropic 只支持 `function_calling` / `json_schema`，没有 `json_mode`。
- **图片输入**：当前生效模型勾选「支持图片」时，用户图片以 `image_url` 内容块直接送给模型（OpenAI 与 Anthropic 都接受该形态）；不支持时提示词里显式声明「你看不到图」，引导用户用文字描述，避免模型按文件名编造内容。
- **对外口径（重要）**：模型配置是管理端能力，用户侧任何文案都不得出现「模拟 / 未配置 / API Key / `.env` / 管理员 / 供应商」等信息。mock 兜底只给通用养护建议；上传失败、工具异常一律用固定话术（工具异常原本会被 `ToolNode` 把异常原文回填给模型，现在统一经 `agent.graph._tool_error_message` 脱敏，知识检索不可用则整轮失败）；`tests/test_user_facing_text.py` 用关键词清单守住这条线。
- **引用可追溯**：命中片段以 `refs` 帧在答案首字前下发，并随助手消息落库（`chat_message.refs`）。
- **韧性**：全局 20 路 / 单用户 2 路并发闸，单条消息 2000 字上限，LLM 首帧静默重试；评论摘要走 Redis + PostgreSQL 两级缓存（推荐结果只走 Redis）；checkpoint 与业务表故障时降级，不阻断对话。

## 安全

- `.env` 含真实 Key，不入库，仅提交 `.env.example`。
- MCP 工具由管理员显式接入，可能带副作用且不走内置工具审批，只接可信服务；`AGENT_MCP_ENABLED=false` 可整体停用。
- 切换 Embedding 模型或维度需重建知识库，否则向量维度不匹配。
