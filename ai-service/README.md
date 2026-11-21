  # PetVerse AI 服务（ai-service）

基于 FastAPI 的 AI 能力微服务，启动后注册进 Nacos（供网关 `lb://ai-service` 发现），通过网关统一入口 `/api/ai/**`（StripPrefix=1）对外提供服务。调用下游业务时同样从 Nacos 获取健康实例并轮询分流，不再配置固定服务地址。内部统一使用 **LangGraph** 编排，对话模型与 Embedding 均为 **OpenAI 兼容格式**（改 `base_url` + `model` 即可切换 DeepSeek / 阿里云百炼等任意服务商）。

## 能力总览

| 能力 | 说明 | 编排图 |
|---|---|---|
| AI 养宠顾问对话 | 自主 AgentLoop（模型自行决定检索 / 查数 / 作答），SSE 流式，多会话管理 | `app/agent/` |
| AI 健康智能评估 | 基于宠物档案 + 健康信息生成评分、风险、养护计划、提醒 | `app/graph/health_graph.py` |
| 商品评论摘要 | 汇总真实评论生成口碑概览（情感 / 优缺点 / 关键词） | `app/graph/review_graph.py` |
| 个性化推荐 | 结合宠物画像、订单、购物车、评价、动态重排推荐 | `app/graph/recommend_graph.py` |
| AI 扩展管理（MCP / Skill） | 管理员配置 MCP 远程工具与 Skill 技能指令，热装配进对话 Agent | `app/ext_api.py` / `app/agent/extensions.py` |

**对话架构（`app/agent`，自主 AgentLoop）**：对话链路不再是一张写死的意图图，而是一个
`prepare → agent ⇄ act → finalize` 的最小循环——**调不调工具、调哪个、调几次、什么时候停，
全部由模型在 `agent` 节点里自行决定**。`prepare` 只做确定性上下文工程（宠物档案、长期记忆、
医疗护栏、系统提示）；`act` 用 LangGraph 官方 `ToolNode` 并行执行工具；工具描述按
「能力 + 适用场景 + 空结果含义」三段式书写，模型据此选工具（知识检索 / 我的宠物 / 订单 /
购物车 / 商品口碑 / 社区热门 / 养护画像）。命中医疗或急症关键词时，Prompt 仍强制附加
「及时咨询专业宠物医生」的就医护栏。

循环有两条确定性边界：`AGENT_MAX_STEPS`（默认 3 轮工具调用）到顶后走 `conclude` 节点、
不绑工具地强制收敛；知识检索不可用时 `RagUnavailable` 直接上抛（不做「无依据也照答」的静默降级）。
新增能力只需写一个 `@tool` 并在 `app/agent/tools/registry.py` 登记分组，不用改图、不用改路由。

**AI 扩展（MCP / Skill，管理员配置）**：除内置工具外，管理员可在前端「AI 扩展管理」页
（`/admin/ai-extensions`，仅 ADMIN）动态接入两类能力：

- **MCP 服务**（存 PostgreSQL `ai_mcp_server`）：经 `langchain-mcp-adapters` 把远程 MCP 服务的工具
  转成 LangChain 工具并入工具列表，支持 `streamable_http` / `sse`（填 url，可带鉴权 headers）与
  `stdio`（填 command + args + env）三种传输，配置字段与标准 `mcpServers` 一致。装配在
  `app/agent/extensions.py`，进程内缓存 + TTL（`MCP_CACHE_TTL`），管理接口写入后立即失效；
  单个服务不可达 / 超时只跳过该服务，依赖未安装或 PG 不可用时整体降级为空，**不阻断对话**。
  MCP 工具与内置工具同名时保留内置工具（内置工具的身份隔离与审批语义经过审计）。
- **Skill 技能**（标准 Agent Skills 范式，存磁盘 `app/skill_store.py`）：每个技能是一个
  `skills/<name>/SKILL.md` 包——YAML frontmatter（`name` hyphen-case / `description` 单行≤1024，
  可选 `enabled`）+ Markdown 正文（≤500 行），可附 `scripts/` `references/` `assets/` 资源目录。
  采用**渐进式披露**：`prepare` 节点只把启用技能的**元数据目录**（name + description）常驻提示词，
  模型判断相关时调用 `load_skill` 工具按需取回正文（`skill` 工具分组始终启用），资源再按需读取——
  避免把全部技能正文每轮塞进上下文。技能根目录可用 `SKILLS_DIR` 配置（默认包内 `skills/`）。

> 信任边界：MCP 工具是管理员显式接入的授信能力，**请只接入可信服务**——外部服务暴露的工具
> 可能带副作用，且不走内置工具的人工审批链路。`AGENT_MCP_ENABLED=false` 可一键停用全部 MCP 装配。

项目自带一对可直接接入的示例：MCP `mcp_servers/petcare_tools.py`（养宠实用计算：年龄换算 /
喂食量 / 饮水量 / 有毒食物风险初筛，均为只读计算，见 `mcp_servers/README.md`）+ Skill
`skills/petcare-calc/SKILL.md`（养护计算与中毒初筛的回答流程与安全口径）。MCP 提供「能算什么」，
Skill 约定「什么时候算、怎么问清输入、怎么呈现、中毒如何安全优先」，二者一同启用既「算得出」又「用得对」。
Skill 经管理页「新增技能」填写 name / description / 正文即落地为 SKILL.md 包，无需手工建文件。

**写操作（用户能点的按钮）**：除只读查询外，模型还能执行加购 / 改数量 / 移出购物车、点赞、评论、
发布动态、登记宠物与更新健康档案。`cart` / `interaction` / `pet_edit` 默认开启（可撤销）；
`content`（公开发布）默认关闭，需显式配置。同一轮内相同工具 + 相同参数只真正执行一次
（`app/agent/idempotency.py`），防止模型重复发调用或路由层首帧重试把加购做两遍。

**能力边界**：AI 的电商能力**止于加入购物车**——下单、支付、取消订单一律不开放给模型，交易闭环
由用户在 App 完成。用户说「帮我下单」时，模型会说明商品已在购物车并引导去结算，不会声称已下单。
商品检索只返回**当前可购买**的商品（上架、有库存、店铺正常营业），并裁掉 `merchantId`、店铺状态、
审核字段等用户看不到的信息；社区动态同样只投影作者昵称、点赞数等展示字段。删除动态 / 评论 / 宠物
不开放给模型。

**敏感操作人工审批（Human-in-the-loop）**：发布动态在工具内调用 `interrupt()`，**整张图会在写库前
挂起**，SSE 下发 `approval` 帧（含可见性与内容摘要），本轮没有 `done`。前端弹审批卡片，用户点
「同意 / 拒绝」后调用 `POST /api/ai/chat/approve`，服务端用 `Command(resume=...)` 从断点继续：
同意才真正下发写请求，拒绝则把「用户未批准」写回 ToolMessage 让模型继续对话。审批现场存在
checkpoint 里，**没有 checkpointer 时直接拒绝执行（失败关闭）**，不会退化成「自动放行」。
中断恢复时被挂起的节点会从头重跑，因此挂起前只做只读查询，写请求全部在审批之后。
设计说明见 `docs/AI自主Agent重构方案.md`。

**对话记忆（LangGraph 官方 checkpoint）**：对话图编译时注入 `PostgresSaver`（`langgraph-checkpoint-postgres`），图状态 `messages` 按 `thread_id = 用户:会话` 在每个超级步自动持久化到 PostgreSQL，下一轮对话自动恢复上下文。用户点击「停止生成」时，路由层把「问题 + 已产出的部分回复（`interrupted` 标记）」经 `aupdate_state` 补写回图状态——被中止的轮次同样进入后续对话的记忆，不再「暂停即失忆」。

**RAG 检索（混合检索）**：`app/kb/` 子模块实现「向量 + 关键词」双通道召回——pgvector 余弦召回（HNSW 索引）与 jieba 分词的进程内 BM25 各取 `RAG_RECALL_K` 条，按加权序值融合（`RAG_FUSION_ALPHA` 偏向向量通道），再按宠物物种做元数据降权/过滤，最后用「语义相似度 或 关键词命中词数」双门槛决定保留。多轮追问会先经 LLM 改写成自包含检索问句（失败自动退回原句）。检索链路不可用时**直接报错**（`RagUnavailable`），不做静默降级——知识问答里"无依据却答得像真的"比明确失败更糟。

**引用来源可追溯**：检索命中的文档（名称 / 类型 md·pdf·docx·doc / 小节 / 相关度 / 摘要）在答案首字之前以 SSE `refs` 帧推给前端，并随助手消息落库（`chat_message.refs` JSONB），历史回放同样可见；System Prompt 要求模型按 `[1] [2]` 标注引用，与来源列表一一对应。

**语料规模与召回率**：`app/knowledge/` 现有 21 篇 md + 1 篇 pdf + 1 篇 docx（约 6.4 万字 / 174 个片段）。`scripts/eval_rag.py` 用 83 条黄金查询做消融评测（判定标准是「答案证据是否进候选」）：

| 模式 | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| 改造前（旧 11 片段库 · 纯向量 top4 · 阈值 0.35） | 9.6% | 12.0% | 12.0% | 0.106 |
| 只换语料与切分（新库 · 纯向量 top4 · 阈值 0.35） | 91.6% | 96.4% | 97.6% | 0.941 |
| 最终混合检索（向量 + BM25 · 物种过滤 · 双门槛） | **92.8%** | **97.6%** | **97.6%** | **0.948** |

结论很直白：**召回率的首要瓶颈是语料覆盖（12% → 97.6%），检索算法是第二顺位（97.6% → 97.6%、hit@1 +1.2pt、MRR +0.007）**——但关键词通道在「黑下巴、猫癣、氯菊酯、MDR1」这类精确术语问法上是必要的兜底。

**会话与存储**：对话按「用户 + 宠物 + 会话」三级隔离；全部业务数据统一落在 PostgreSQL（`petverse_ai`）——`chat_session` / `chat_message` 为会话与消息持久层（前端展示 / 会话管理，被中止的轮次同样兜底落库并以 `interrupted` 列标记）、LangGraph checkpoint 承载 LLM 上下文记忆、健康评估报告入 `pet_health_report`；评论摘要为两级缓存（Redis 热 + PostgreSQL `ai_cache` 温兜底），推荐结果只使用 Redis 五分钟热缓存。

## 环境要求

- Python 3.12
- 可访问的 Nacos（默认 `localhost:8848`）
- 可访问的 Redis（默认 `localhost:6379`，密码 `123456`，AI 模块统一使用 db=1）
- 可访问的 PostgreSQL + **pgvector 插件**（默认 `localhost:5432`，库 `petverse_ai`；会话与消息、对话记忆 checkpoint、RAG 语义检索、健康报告都在这里；缺失时聊天记忆等自动降级，知识检索则直接报错）
- LLM API Key（OpenAI 兼容，可选：留空或 `MOCK_CHAT=true` 时走内置 mock 回复）
- Embedding API Key（OpenAI 兼容；**知识检索必需**，不配置时知识类提问直接报错）

## 初始化数据库

```powershell
# 数据库 petverse_ai 需先存在：CREATE DATABASE petverse_ai;
# 业务表（chat_session / chat_message）+ pgvector 扩展 + 健康报告 / 缓存表 + MCP 配置（ai_mcp_server）
psql -U postgres -d petverse_ai -f db/schema_pgvector.sql
# 会话 / 消息表、MCP 配置表与 checkpoint 表都会在服务启动时自动创建（幂等），本脚本为手动入口与表结构文档
# Skill 技能不落库，是磁盘标准技能包 skills/<name>/SKILL.md（见 app/skill_store.py）
```

存量数据迁移（原 MySQL 库 → PostgreSQL，一次性、幂等，保留原会话 ID）：

```powershell
.venv\Scripts\pip install aiomysql        # 仅迁移脚本需要，完成后可卸载
.venv\Scripts\python -m scripts.migrate_mysql_to_pg
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
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | RAG 向量化（OpenAI 兼容）。阿里云百炼 `text-embedding-v3`（dim 支持 1024/768/512）。**不配置则知识检索不可用（知识类提问明确报错）** |
| `MOCK_CHAT` | `true` 强制内置模拟回复，无需真实调用 |
| `PG_*` | PostgreSQL + pgvector（RAG 向量库 / 健康报告 / LangGraph checkpoint 对话记忆 / 结果缓存温层 `ai_cache`） |
| `RAG_ENABLED` / `RAG_TOP_K` / `RAG_RECALL_K` | RAG 开关：注入 Prompt 的片段数 / 每条通道的候选召回数 |
| `RAG_MIN_SIM` / `RAG_LEX_MIN_TERMS` / `RAG_FUSION_ALPHA` | 向量门槛 / 关键词命中词数门槛 / 融合时向量通道权重（0~1，越大越偏向量） |
| `RAG_SPECIES_STRICT` / `RAG_SPECIES_PENALTY` | 物种不符时硬过滤或按系数降权 |
| `RAG_QUERY_REWRITE` / `RAG_REWRITE_MIN_CHARS` | 多轮追问改写开关 / 短于此长度必定改写 |
| `KNOWLEDGE_DIR` / `KB_CHUNK_*` / `EMBED_BATCH_SIZE` | 语料目录 / 切分长度（目标·合并下限·硬上限）/ 单批 Embedding 条数 |
| `KB_ADMIN_TOKEN` / `KB_MAX_UPLOAD_MB` | 知识库运维备选令牌（管理员角色之外的第二条通道，可留空）/ 上传大小上限 |
| `SKILLS_DIR` | Skill 技能包根目录（标准 `skills/<name>/SKILL.md`），留空默认包内 `skills/` |
| `NACOS_SERVER_ADDR` / `NACOS_GROUP` / `NACOS_NAMESPACE` | Nacos 地址 / 分组 / 命名空间；注册与下游发现共用，须与 Java 服务一致，命名空间留空表示 `public` |
| `SERVICE_DEPLOY_MODE` | `microservice`（默认）：按各服务名发现；`monolith`：所有业务请求统一路由到单体服务 |
| `PET_SERVICE_NAME` 等 `*_SERVICE_NAME` | 微服务模式下各业务域在 Nacos 中的服务名，默认与 Java 的 `spring.application.name` 一致 |
| `MONOLITH_SERVICE_NAME` | 单体模式的目标服务名，默认 `petverse-single` |
| `SERVICE_DISCOVERY_CACHE_TTL` | 健康实例列表的本地缓存秒数（默认 10）；到期单飞刷新，Nacos 网络故障时可暂用旧列表 |
| `HTTP_TIMEOUT` | 已选中实例的业务 HTTP 调用超时秒数（默认 8） |
| `REDIS_DB` | AI 模块统一使用的 Redis 库（默认 1；结果缓存与并发计数共用） |
| `REVIEW_SUMMARY_TTL` / `RECOMMEND_CACHE_TTL` | 缓存 TTL（秒）：评论摘要 6 小时（Redis + `ai_cache` 两级）；推荐 300 秒（仅 Redis，未命中直接重算业务） |
| `AGENT_MAX_STEPS` / `AGENT_TOOL_GROUPS` | 单轮最多几轮「模型 → 工具」循环（超限强制收敛）/ 启用的工具分组（默认 `knowledge,pet,shop,cart,community,interaction,pet_edit,publish,profile`；`publish` 发布动态默认开启但每次都会挂起等审批，`content` 评论默认关闭，关掉即让模型看不见该组工具） |
| `AGENT_APPROVAL_TOOLS` | 需要人工审批的工具（默认 `publish_post`）；命中时图挂起等前端审批，依赖 checkpoint，无 checkpointer 时直接拒绝执行 |
| `AGENT_MCP_ENABLED` / `MCP_LOAD_TIMEOUT` / `MCP_CACHE_TTL` | MCP 扩展总开关（默认 true）/ 单服务连接与列工具超时秒数（默认 10）/ 扩展配置进程内缓存 TTL 秒数（默认 60，管理接口写入会立即失效，TTL 兜底多 worker 跨进程刷新） |
| `HISTORY_MAX_MESSAGES` | LLM 上下文窗口条数（从 checkpoint 记忆裁剪最近 N 条） |
| `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` | 生成参数 |

### 服务发现与部署模式

微服务部署保持默认配置即可；服务名需要与 Nacos 控制台中的名称一致：

```dotenv
SERVICE_DEPLOY_MODE=microservice
PET_SERVICE_NAME=pet-service
SPACE_SERVICE_NAME=space-service
USER_SERVICE_NAME=user-service
SOCIAL_SERVICE_NAME=social-service
REMARK_SERVICE_NAME=remark-service
SHOP_SERVICE_NAME=shop-service
DATA_SERVICE_NAME=data-service
```

服务名必须与 Nacos 注册名一致；原固定地址配置已由这组服务名替代，不再通过环境变量保存任何业务实例 IP / 端口。单体部署（包括单体多实例）只需设置以下两项，其余业务服务名不会参与路由：

```dotenv
SERVICE_DEPLOY_MODE=monolith
MONOLITH_SERVICE_NAME=petverse-single
```

两种模式都会过滤不健康、已禁用或权重为 0 的实例，并在实例间轮询。单体模式把 `/pet/**`、`/space/**`、`/shop/**`、`/data/**` 等路径直接请求到选中的单体实例，不再分别发现各业务服务名；有多个 `petverse-single` 实例时仍会自动分流。实例列表默认缓存 10 秒，Nacos 刷新请求失败时可暂用旧列表，但 Nacos 明确返回无健康实例时不会继续访问旧地址。

> pgvector 维度必须与 `EMBEDDING_DIM` 一致（`kb_chunk.embedding vector(1024)`）；切换 Embedding 模型需重建知识库表。

## 知识库语料与导入

语料目录 `app/knowledge/`（可用 `KNOWLEDGE_DIR` 改），支持 **md / pdf / docx / doc** 四种格式，递归扫描子目录：

- **md**：可在文件开头用 `---` 元数据块声明 `title` / `category` / `species` / `tags`（`tags` 会进 BM25 词表——正文里没写的俗称，如「黑下巴、猫痤疮」，靠标签才能被关键词命中）；正文按 Markdown 标题层级切分成小节，片段自带「文档标题｜分类｜小节」上下文前缀。
- **docx**：按段落样式（Heading 1/2/3、标题 1/2/3）还原层级，表格转成「表头：值」文本，不丢内容。
- **pdf**：PyMuPDF 逐页抽取（按阅读顺序），页码作为小节路径。
- **doc**（Word 97-2003 二进制）：调用系统 `antiword` / `catdoc` 抽文本，工具缺失时明确报错提示先转存 docx（不静默产出空文档）。

```powershell
# 增量导入（内容 sha256 未变的文档直接跳过，省 Embedding 调用）
python -m scripts.ingest_knowledge
python -m scripts.ingest_knowledge --full     # 清空后全量重建
python -m scripts.ingest_knowledge --force    # 不跳过，全部重新向量化
python -m scripts.ingest_knowledge --file x.pdf
python -m scripts.ingest_knowledge --stats    # 查看当前规模与文档清单
```

按 `doc_id`（文件名主干）覆盖写入 `kb_document` + `kb_chunk`，单事务「删旧片段 → 写文档行 → 写片段」，并自增 `kb_meta.version` 让 BM25 索引惰性重建。单个文件解析失败只进失败清单、不中断整轮导入，脚本以非零码退出。

**原文留存与在线预览**：`kb_document.file_path` 记录原文位置——仓库文档直接记源文件路径，
上传接口收到的文件按 `doc_id` 落盘到 `KB_STORAGE_DIR`（默认 `data/kb`），删除上传文档时一并清理。
预览接口优先重解析原文（排版完整、无切分重叠），原文缺失时回退按 `chunk_index` 拼接片段；
管理页的「AI 入库片段」页签则直接展示切分后真正进向量库的内容（含上下文标题行、字数与检索词数），
排查召回问题时看这个比看原文更有用。Markdown 正文在前端经 DOMPurify 消毒后才 `v-html`——
文档内容来自上传文件，不过消毒等于把「上传文档」变成向页面注入脚本的通道。

评测与调参：

```powershell
python -m scripts.eval_rag --validate   # 校验黄金集期望子串确实存在于知识库
python -m scripts.eval_rag              # 跑 A/B/D 三模式消融对比
```

## 启动服务

```powershell
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8086
```

服务发现与路由的单元测试（无需启动 Nacos）：

```powershell
python -m unittest discover -s tests -v
```

## 接口列表

| 方法 | 网关路径（前端调用） | 说明 |
|---|---|---|
| POST | `/api/ai/chat/stream` | SSE 流式对话；体 `{"message","pet":{...},"sessionId"}`；首帧 `meta` 回传 sessionId，随后 `refs` 帧推送引用来源；命中敏感操作时下发 `approval` 帧并挂起 |
| POST | `/api/ai/chat/approve` | 人工审批敏感操作并从断点继续；体 `{"sessionId","approvalId","approved","reason"}`，返回 SSE 流 |
| GET | `/api/ai/chat/history?sessionId=` | 查询会话历史（PostgreSQL，时间正序；助手消息带 `refs`） |
| GET | `/api/ai/chat/sessions` | 用户全部会话（跨宠物，最近活跃在前） |
| DELETE | `/api/ai/chat/sessions/{id}` | 删除会话（连带消息与 checkpoint 记忆） |
| GET | `/api/ai/kb/documents?page=&size=&keyword=` | 知识库文档列表（管理员限定；服务端分页 + 名称/文件名/分类模糊过滤，返回 items/total/pages 与全库统计） |
| GET | `/api/ai/kb/documents/{docId}/preview` | 文档在线预览（管理员限定；正文优先重解析原文，原文缺失回退片段拼接，附入库片段清单） |
| GET | `/api/ai/kb/documents/{docId}/file?inline=` | 回读文档原文（管理员限定；PDF 供浏览器内置查看器渲染，路径限定在语料/留存目录内） |
| POST | `/api/ai/kb/documents` | 上传知识文档（multipart：`file` + 可选 `title`/`category`/`species`）→ 解析切分入库（管理员限定） |
| DELETE | `/api/ai/kb/documents/{docId}` | 删除文档（管理员限定；`kb_chunk` 外键级联删除含向量的片段，BM25 版本戳 +1 触发索引重建；上传文档的留存原文一并清理，**仓库语料源文件不动**——重新全量重建会把它带回来） |
| POST | `/api/ai/kb/reindex` | 用仓库语料目录全量重建知识库（管理员限定；同一时刻仅允许一个） |
| GET | `/api/ai/admin/mcp/servers` | MCP 服务列表（管理员限定；含停用项） |
| POST | `/api/ai/admin/mcp/servers` | 新增 MCP 服务（管理员限定；体为 name/transport/url/command/args/env/headers/description/enabled） |
| PUT | `/api/ai/admin/mcp/servers/{id}` | 整体更新 MCP 服务（管理员限定；启停开关也走这里，提交完整配置） |
| DELETE | `/api/ai/admin/mcp/servers/{id}` | 删除 MCP 服务（管理员限定） |
| POST | `/api/ai/admin/mcp/servers/{id}/test` | 测试 MCP 连通性并列出其工具（管理员限定；停用中的服务也可测试） |
| GET | `/api/ai/admin/skills` | Skill 技能包列表（管理员限定；含停用项与正文） |
| POST | `/api/ai/admin/skills` | 新增 Skill（管理员限定；体为 name(hyphen-case)/description(单行)/content/enabled，落地 SKILL.md 包） |
| PUT | `/api/ai/admin/skills/{name}` | 整体更新 Skill（管理员限定；按技能名，启停也走这里，改名迁移目录） |
| DELETE | `/api/ai/admin/skills/{name}` | 删除 Skill 技能包（管理员限定；整目录删除） |
| POST | `/api/ai/health/assess` | 宠物健康评估；体为宠物档案（含 `health`），返回结构化报告 |
| GET | `/api/ai/health/history?petId=` | 宠物历史健康评估 |
| POST | `/api/ai/shop/review/summary` | 商品评论摘要；体 `{"productId"}` |
| GET | `/api/ai/recommend/feed?scene=home\|shop&refresh=` | 个性化推荐流 |

说明：

- 网关统一 JWT 鉴权后注入 `X-User-Id`；缺失 / 非法返回 `{"code":401,"msg":"未登录"}`。所有业务响应为 HTTP 200 + `{code,msg,data}`。
- SSE 事件：`meta` → `refs`（有知识命中时）→ `delta` * n → `done`；命中人工审批时，在 `delta` 之后下发 `approval` 并结束本次流（**没有 `done`**），前端调 `/api/ai/chat/approve` 继续；异常时 `error` 后结束流。`refs` 载荷为 `{"type":"refs","items":[{rank,docId,source,docTitle,kind,section,category,score,matchedBy,snippet}]}`，在答案首字之前到达；`approval` 载荷为 `{"type":"approval","approvalId","action","title","summary":[{label,value}],"resource"}`。
- **知识库 `/api/ai/kb/*` 与 AI 扩展管理 `/api/ai/admin/*` 接口一律管理员限定**：凭网关注入的 `X-User-Role`（网关会先剥离客户端同名头，值取自 JWT 的角色声明）判定，非 `ADMIN` 返回 `{"code":403,...}`；网关（含单体版 `GatewayAuthFilter`）对 `/api/ai/admin` 前缀也做 ADMIN 校验，与本服务内的校验构成纵深防御。`KB_ADMIN_TOKEN` / 请求头 `X-Kb-Admin` 是留给「不经网关直连本服务跑运维脚本」的**备选凭证**，与管理员角色二选一即可——没有这条通道时，任何能直接访问 8086 的进程伪造一个 `X-User-Role: ADMIN` 就能改写全用户共享的检索语料与扩展配置。前端路由 `meta.roles: ['ADMIN']` 与顶栏入口同样只对管理员可见。
- 评论摘要结果缓存于 Redis + PostgreSQL `ai_cache` 两级；推荐结果只缓存于 Redis（5 分钟），Redis 未命中时直接重新查询业务数据，`refresh=true` 可跳过推荐缓存强制刷新。
- Embedding 或向量库不可用时知识类提问直接报错（不降级）；知识库为空时报错提示先跑导入脚本。

## 安全提示

- `.env` 含真实 API Key，请勿提交到仓库（本仓库仅提交 `.env.example`）。
- 若曾提交过 Key，请立即在服务商控制台轮换。

## 生产环境韧性设计

AI 对话链路长（浏览器 → 网关 → ai-service → LLM 服务商 → Redis / PostgreSQL），
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

- **评论摘要结果缓存两级降级**：热层 Redis + 温层 PostgreSQL `ai_cache`，
  由 `app/cache.py` 门面统一读写——读：Redis 未命中自动回落 ai_cache；写：两级并行双写；
  Redis 故障时读自动走 ai_cache、写仅落 ai_cache，仅两级都不可用才退化为重新计算；
  任何缓存异常均内部静默降级，主流程不受影响（恢复后 redis-py 自动重连）。
- **推荐缓存只走 Redis**：`ai:recommend:v2:{scene}:{userId}` TTL 300 秒，
  Redis 未命中直接重新查询业务数据并回填，不读也不写 `ai_cache`；
  同一 Key 仍通过进程内单飞锁避免并发重算。
- **LangGraph checkpoint（对话记忆）**：官方 `PostgresSaver`（同步存储引擎）+ 线程适配层——
  本服务运行在 Windows 上，psycopg 异步连接在默认 ProactorEventLoop 下不可用（与 pg_store 同款约束），
  故异步方法统一经线程池代理执行；PG 故障时读降级为「无历史」、写静默失败，对话不受影响；
  建表失败带 30s 冷却重试，PG 恢复后无需重启服务即可重新启用记忆。
- **PostgreSQL 会话与消息持久化**：读降级为空历史、写仅记日志；连接池采用
  psycopg_pool（惰性连接 + 自有断线重连与坏连接淘汰），PG 重启 / 网络闪断后
  无需重启服务即可自愈；业务表缺失时带 30s 冷却自动补建。
- **会话操作（建 / 删 / 查）**：失败向上抛，由路由层转成统一业务错误报文（不静默降级）。

### 4. 断连与异常收尾

- **SSE 心跳**：15s 无 token 产出即发注释行 ping，防止网关 / 代理 idle 超时断连。
- **客户端中途断开 / 用户点击「停止生成」**：问题与已产出的部分回复被补写进两处存储——
  LangGraph checkpoint（`aupdate_state` 追加进图状态，部分回复标记 `interrupted`，
  成为后续对话的长期记忆）与 PostgreSQL（`save_turn_if_exists` 兜底保存，供前端历史回放，
  会话已删则按会话表原子跳过，不复活历史）；**用户消息无条件落库**（即使中止时零产出，
  首轮提问也不会丢）；`asyncio.shield` 保证取消路径的写入落地。
- **统一报文契约**：所有错误均为 HTTP 200 + `{code,msg,data}`，前端不会收到裸 5xx 或英文技术报错。
