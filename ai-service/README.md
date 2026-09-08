# PetVerse AI 服务（ai-service）

AI 养宠顾问对话微服务：基于 FastAPI 构建，启动后注册进 Nacos（供网关 `lb://ai-service` 发现），通过网关统一入口 `/api/ai/**`（StripPrefix=1）对外提供宠物健康、习性咨询的**流式对话（SSE）**与多会话管理能力。对话按「用户 + 宠物 + 会话」三级隔离：一只宠物可拥有多个会话，切换宠物 / 新建对话均进入待创建态、会话在发出首条消息时才落库（避免频繁切换堆积空会话），历史会话可手动选择恢复，会话支持删除；前端侧栏直接展示该用户与所有宠物的全部历史会话（每条带归属宠物 petId），无需先选宠物。对话由 DeepSeek（OpenAI 兼容 API）驱动，助手以中立的养宠顾问身份回答（不扮演宠物、无语气化人设）；未配置 Key 或开启 `MOCK_CHAT` 时自动切换为内置 mock 回复；多轮对话采用**双层存储**：Redis（db=3）作为 LLM 短窗口上下文缓存（按会话滚动保留最近 N 条 + TTL），MySQL（`petverse_ai.chat_session` / `petverse_ai.chat_message`）作为会话与消息持久层（服务重启 / Redis 过期都不丢历史），两者写入时并行、删除会话时同步清理，历史查询以 MySQL 为准。

## 环境要求

- Python 3.12（本机使用固定解释器 `C:\Users\37442\python\Python3.12.6\python.exe`）
- 可访问的 Nacos（默认 `localhost:8848`，server 2.4.3）
- 可访问的 Redis（默认 `localhost:6379`，密码 `123456`，db=3）
- 可访问的 MySQL（默认 `localhost:3306`，用户 `root`，密码 `123456`，库 `petverse_ai`）
- DeepSeek API Key（可选，mock 模式下不需要）

## 初始化数据库

首次使用需创建 `petverse_ai` 库与 `chat_session`（会话表）、`chat_message`（消息表）：

```powershell
mysql -uroot -p123456 < db/schema.sql
```

或在 MySQL 客户端中直接执行 `db/schema.sql` 内的 SQL。旧版已建过 `chat_message` 的库，按脚本尾部注释执行一次 `ALTER TABLE` 升级（补 `session_id` 列与索引）即可。

## 创建虚拟环境（PowerShell）

```powershell
cd d:\Java\PetVerse_qiuzhao\PetVerse\ai-service
& 'C:\Users\37442\python\Python3.12.6\python.exe' -m venv .venv
```

## 安装依赖

```powershell
.venv\Scripts\pip install -r requirements.txt
```

网络较慢时可使用清华镜像：

```powershell
.venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 配置说明（.env）

首次使用请复制 `.env.example` 为 `.env` 并按需修改（当前仓库内 `.env` 已配置为本地 mock 模式）。

| 配置项 | 说明 |
|---|---|
| `AI_SERVICE_NAME` | 注册到 Nacos 的服务名，网关路由 `lb://ai-service` 依赖它 |
| `AI_SERVICE_IP` / `AI_SERVICE_PORT` | 本实例注册地址与监听端口（默认 `127.0.0.1:8086`） |
| `NACOS_SERVER_ADDR` | Nacos 服务端地址 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key；**留空时自动进入 mock 模式** |
| `DEEPSEEK_BASE_URL` | DeepSeek 的 OpenAI 兼容接口地址 |
| `DEEPSEEK_MODEL` | 对话模型名（默认 `deepseek-chat`） |
| `MOCK_CHAT` | mock 开关：`true` 时强制使用内置模拟回复，无论是否配置了 Key |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | Redis 连接信息（对话记忆缓存存 db=3） |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB` | MySQL 连接信息（会话与对话消息持久化存储，默认库 `petverse_ai`） |
| `MYSQL_POOL_MIN` / `MYSQL_POOL_MAX` | aiomysql 连接池最小 / 最大连接数（默认 1 / 10） |
| `HISTORY_MAX_MESSAGES` | 每个会话最多保留的历史消息条数（默认 40，仅作用于 Redis LLM 上下文；MySQL 持久层不裁剪） |
| `HISTORY_TTL_SECONDS` | Redis 历史过期时间（默认 604800 秒 = 7 天，每次写入滚动续期；MySQL 持久层不受此限制） |
| `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` | LLM 生成参数 |

### MOCK_CHAT 开关与真实模式切换

- 当前 `.env` 中 `MOCK_CHAT=true`，即使已配置 `DEEPSEEK_API_KEY` 也会强制走 **mock 模式**，
  对话接口返回内置的模拟咨询回复（打字机节奏逐块输出），无需真实调用 DeepSeek，用于联调验证。
- 切换真实模式：将 `MOCK_CHAT` 改为 `false` 即启用真实 DeepSeek 调用（`DEEPSEEK_API_KEY` 需为有效 Key，留空则仍回退 mock 模式），重启服务即可。

## 启动服务

```powershell
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8086
```

（需在 `ai-service` 目录下执行；也可直接 `.venv\Scripts\python -m app.main`）

## 接口列表

| 方法 | 网关路径（前端调用） | 说明 |
|---|---|---|
| POST | `/api/ai/chat/stream` | SSE 流式对话；请求体 `{"message": "...", "pet": {id, name, species, breed, age, ...}, "sessionId": 1}`（pet 字段均可选；sessionId 缺省时自动新建会话并通过首帧 meta 事件回传） |
| GET | `/api/ai/chat/history?sessionId={id}` | 查询指定会话的对话历史（时间正序：旧 → 新） |
| GET | `/api/ai/chat/sessions` | 查询用户的全部会话（跨宠物统一展示，最近活跃在前，每条带归属宠物 petId） |
| DELETE | `/api/ai/chat/sessions/{sessionId}` | 删除会话（连带会话下全部消息与 Redis 缓存） |

说明：

- 网关统一 JWT 鉴权后向下游注入 `X-User-Id` 请求头，本服务依赖该头识别用户；缺失 / 非法时返回 `{"code":401,"msg":"未登录","data":null}`。会话读写前均校验归属，防止跨用户 / 跨宠物串会话（非法 sessionId 返回 `{"code":404,"msg":"会话不存在或已删除"}`）。
- 流式对话响应为 `text/event-stream`，事件格式：`data: {"type":"meta","sessionId":1}`（首帧）→ `data: {"type":"delta","content":"..."}`（多次）→ `data: {"type":"done"}`；异常时输出 `data: {"type":"error","msg":"AI 服务暂时开小差了，请稍后再试"}` 后结束流。
- 网关侧已为 `/api/ai/**` 配置 `response-timeout: 120000`，长回复不会被网关提前掐断。
