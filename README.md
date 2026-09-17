# PetVerse · 智慧养宠社区平台（后端）

> 面向宠物爱好者的微服务社区平台后端，覆盖「虚拟宠物养成 + 真实宠物档案管理 + 社区社交 + 周边商城」四大业务域，并自研 AI 服务提供养宠顾问对话、健康智能评估、评论摘要与个性化推荐能力。

本仓库为 **后端代码**，采用 **Java + Python 混合微服务**：Java 侧包含网关、用户、宠物、宠域空间（动态）、社交、点赞评论通知、商城共 8 个服务；AI 能力独立为 Python（FastAPI、langchain\LangGragh）服务，注册进 Nacos 后由 Spring Cloud Gateway 统一路由（`/api/ai/**`），与 Java 体系共享网关鉴权与服务发现。

> 前端代码见独立仓库: https://github.com/jieHao333/PetVerse-web。

## 目录

- [技术栈](#技术栈)
- [服务与架构](#服务与架构)
- [模块说明](#模块说明)
- [快速开始](#快速开始)
- [配置与环境变量](#配置与环境变量)
- [数据库初始化](#数据库初始化)
- [API 概览](#api-概览)
- [项目亮点](#项目亮点)
- [安全与密钥说明](#安全与密钥说明)
- [常见问题](#常见问题)

## 技术栈

| 分类 | 选型 |
|---|---|
| 语言 / 运行时 | Java 21、Python 3.12 |
| 微服务框架 | Spring Boot 3.4.13、Spring Cloud 2024.0.3、Spring Cloud Alibaba 2023.0.3.4 |
| 网关 | Spring Cloud Gateway（WebFlux） |
| 注册 / 配置中心 | Nacos |
| ORM | MyBatis-Plus 3.5.9（逻辑删除、雪花 ID） |
| 认证 | JWT（jjwt 0.12.7） |
| 存储 | MySQL 8、Redis、阿里云 OSS（aliyun-sdk-oss 3.18.1） |
| 检索 | Elasticsearch 8.15.5（商品 / 动态全文检索，故障自动降级 MySQL 模糊查询） |
| 消息队列 | RocketMQ 2.3.3（事件驱动、订单超时延迟消息） |
| AI 服务 | FastAPI + LangChain / LangGraph、PostgreSQL + pgvector |

## 服务与架构

所有请求经网关统一入口 `http://localhost:8080/api/**`，按 `Path=/api/{service}/**` + `StripPrefix=1` 路由到对应服务；网关完成 JWT 鉴权后剥离并注入内部信任头 `X-User-Id`，实现 Java / Python 跨语言的身份透传与越权防护。

| 服务 | 端口 | 语言 | 网关前缀 | 职责 |
|---|---|---|---|---|
| gateway-service | 8080 | Java | — | 统一路由、JWT 鉴权、跨域、内部信任头注入 |
| pet-service | 8081 | Java | `/api/pet/**` | 宠物档案、虚拟宠物养成（经验/等级）、健康信息、身份证 |
| space-service | 8082 | Java | `/api/space/**` | 宠域空间动态（可见性/热度榜）、媒体上传、ES 检索 |
| user-service | 8083 | Java | `/api/user/**` | 注册登录、JWT 签发、用户资料、头像上传、角色 |
| social-service | 8084 | Java | `/api/social/**` | 好友关系、私聊会话与消息、聊天文件 |
| remark-service | 8085 | Java | `/api/remark/**` | 通用点赞、评论、通知（独立通用服务） |
| ai-service | 8086 | Python | `/api/ai/**` | AI 养宠对话、健康评估、评论摘要、个性化推荐 |
| shop-service | 8087 | Java | `/api/shop/**` | 商品、购物车、订单、商家入驻、评价、ES 搜索 |

```
浏览器 / 前端
      │
      ▼
 Spring Cloud Gateway :8080  ──(JWT 鉴权 + 注入 X-User-Id)──┐
      │                                                     │
      ├── /api/user/**   ──▶ user-service :8083             │
      ├── /api/pet/**    ──▶ pet-service  :8081             │
      ├── /api/space/**  ──▶ space-service :8082            │  Nacos（注册 / 配置）
      ├── /api/social/** ──▶ social-service :8084           │  RocketMQ（事件驱动）
      ├── /api/remark/** ──▶ remark-service :8085           │  Redis / MySQL / ES
      ├── /api/shop/**   ──▶ shop-service  :8087            │  PostgreSQL + pgvector
      └── /api/ai/**     ──▶ ai-service    :8086 (FastAPI) ─┘
```

公共能力（结果封装、全局异常、JWT 上下文、MQ 发布/去重、OSS 上传、雪花序列化为字符串等）统一下沉在 `petverse-common` 模块，各业务服务依赖复用。

## 模块说明

```
PetVerse/
├── petverse-common/     # 公共模块：result/exception/context/mq/oss/entity/enums/dto/vo/util
├── gateway-service/     # 网关：路由 + 鉴权 + 跨域
├── user-service/        # 用户与认证
├── pet-service/         # 宠物档案 / 养成 / 健康
├── space-service/       # 宠域空间动态
├── social-service/      # 好友与私聊
├── remark-service/      # 点赞 / 评论 / 通知
├── shop-service/        # 商城：商品 / 购物车 / 订单 / 商家 / 评价
├── ai-service/          # Python AI 服务（详见 ai-service/README.md）
├── db / */resources/db/ # 各服务建表脚本 schema.sql
└── docs/                # 后端开发规范、配置与密钥说明、项目介绍
```

## 快速开始

### 环境要求

- JDK 21、Maven 3.9+（或项目自带 `mvnw`）
- Python 3.12（运行 ai-service）
- 中间件：MySQL 8、Redis、Nacos 2.x、RocketMQ 5.x（可选，未启动时相关能力自动降级）
- 可选：Elasticsearch 8.15.5（全文检索）、PostgreSQL + pgvector（AI 服务）

### 1. 启动中间件

先启动 MySQL、Redis、Nacos（默认 `localhost:8848`）；RocketMQ（`localhost:9876`）、Elasticsearch（`localhost:9200`）可按需启动，未启动时系统会自动降级（消息发送记日志、搜索回落数据库模糊查询）。

### 2. 初始化数据库

见 [数据库初始化](#数据库初始化)，为每个服务创建独立库并执行对应 `schema.sql`。

### 3. 配置密钥 / 环境变量

Java 服务通过环境变量注入敏感配置，Python AI 服务通过 `.env` 文件。详见 [配置与环境变量](#配置与环境变量) 与 [docs/配置与密钥说明.md](docs/配置与密钥说明.md)。

### 4. 编译并安装公共模块

`petverse-common` 是所有服务的依赖，需先安装到本地仓库（先父 pom，再 common）：

```powershell
# 在 PetVerse/ 目录下
mvn -N install              # 安装父 pom
mvn -pl petverse-common install   # 安装公共模块
```

### 5. 启动 Java 服务

在各自目录下启动（或 IDE 中运行对应 `*Application`），建议先启动 user-service 与 gateway-service：

```powershell
mvn -pl user-service   spring-boot:run
mvn -pl pet-service    spring-boot:run
mvn -pl space-service  spring-boot:run
mvn -pl social-service spring-boot:run
mvn -pl remark-service spring-boot:run
mvn -pl shop-service   spring-boot:run
mvn -pl gateway-service spring-boot:run
```

### 6. 启动 AI 服务

详见 [ai-service/README.md](ai-service/README.md)：

```powershell
cd ai-service
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env    # 填入真实密钥（.env 已被 .gitignore 忽略）
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8086
```

## 配置与环境变量

为便于开源，所有密钥与连接信息均已从 `application.yml` 中移除，改为「环境变量 + 开发默认值」的形式（`${VAR:default}`）。生产环境通过环境变量覆盖即可。

**本地开发**：真实密钥放在各服务的 `src/main/resources/secrets-local.yml`（**已被 `.gitignore` 忽略，不会提交**），由 `spring.config.import: optional:classpath:secrets-local.yml` 自动加载，作为占位符的本地默认值，本地迭代不受影响：

```yaml
local:
  oss:
    access-key-id: <AccessKey Id>
    access-key-secret: <AccessKey Secret>
```

OSS 密钥取值优先级：**环境变量 > secrets-local.yml > 空**。涉及 OSS 的 5 个服务（user / pet / space / social / shop）各保留一份该文件；克隆本仓库后如需上传功能，按上述格式新建并填入自己的 AccessKey 即可（密钥获取：阿里云控制台 → RAM 访问控制 → AccessKey 管理），不创建也不影响启动与其它功能。

| 变量 | 用途 | 默认值 |
|---|---|---|
| `MYSQL_URL` | 各服务 JDBC 连接串 | 各自 `localhost:3306/petverse_*` |
| `MYSQL_USERNAME` | MySQL 用户名 | `root` |
| `MYSQL_PASSWORD` | MySQL 密码 | `123456` |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | Redis 连接（remark-service 等） | `localhost` / `6379` / `123456` |
| `JWT_SECRET` | JWT 签名密钥，**所有服务必须一致**，生产环境务必覆盖 | 开发占位值 |
| `OSS_ENDPOINT` | 阿里云 OSS 节点 | `oss-cn-beijing.aliyuncs.com` |
| `OSS_ACCESS_KEY_ID` | OSS AccessKey Id | 本地由 `secrets-local.yml` 提供；未配置时上传抛业务异常 |
| `OSS_ACCESS_KEY_SECRET` | OSS AccessKey Secret | 同上 |
| `OSS_BUCKET_NAME` | OSS 桶名 | `petverse-me` |
| `OSS_DOMAIN` | 可选自定义 / CDN 域名 | 空 |

AI 服务（Python）密钥通过 `ai-service/.env` 管理，仓库仅提交 `.env.example` 模板（`LLM_API_KEY`、`EMBEDDING_API_KEY` 等默认为空，留空即进入 mock / 关键词降级模式）。完整清单见 [docs/配置与密钥说明.md](docs/配置与密钥说明.md)。

## 数据库初始化

每个 Java 服务拥有独立库，建表脚本位于各服务 `src/main/resources/db/schema.sql`：

| 库 | 脚本 |
|---|---|
| `petverse_user` | `user-service/src/main/resources/db/schema.sql` |
| `petverse_pet` | `pet-service/src/main/resources/db/schema.sql` |
| `petverse_space` | `space-service/src/main/resources/db/schema.sql` |
| `petverse_social` | `social-service/src/main/resources/db/schema.sql` |
| `petverse_remark` | `remark-service/src/main/resources/db/schema.sql` |
| `petverse_shop` | `shop-service/src/main/resources/db/schema.sql` |
| `petverse_ai`（PostgreSQL + pgvector） | `ai-service/db/schema_pgvector.sql` |

```powershell
# 先建库，再导入对应脚本
mysql -u root -p -e "CREATE DATABASE petverse_user DEFAULT CHARSET utf8mb4;"
mysql -u root -p petverse_user < user-service/src/main/resources/db/schema.sql
# ... 其余服务同理
```

> AI 服务的会话 / 消息表与 LangGraph checkpoint 表在服务启动时会自动幂等创建，`schema_pgvector.sql` 为手动入口与表结构文档。

## API 概览

统一响应结构 `{ code, msg, data }`；除 `/api/user/login`、`/api/user/register` 白名单外，其余接口需携带 JWT（网关鉴权后注入 `X-User-Id`）。各服务控制器基础路径：

| 服务 | 路径前缀（网关） | 说明 |
|---|---|---|
| user | `/api/user` | 注册、登录、资料、头像上传 |
| pet | `/api/pet` | 宠物档案、养成、健康信息 |
| space | `/api/space` | 动态发布/查询、可见性、热度、媒体 |
| social | `/api/social/friend`、`/api/social/chat` | 好友、私聊会话与消息 |
| remark | `/api/remark/like`、`/api/remark/comment`、`/api/remark/notification` | 点赞、评论、通知 |
| shop | `/api/shop/product`、`/cart`、`/order`、`/review`、`/merchant`、`/apply`、`/admin`、`/store`、`/file` | 商城与商家 |
| ai | `/api/ai/chat/stream`、`/api/ai/health/assess`、`/api/ai/shop/review/summary`、`/api/ai/recommend/feed` | AI 能力（SSE 流式对话等，详见 ai-service README） |

## 项目亮点

1. **独立设计实现 AI 对话编排引擎**：基于 LangGraph StateGraph 将「意图识别 → RAG 检索 / Agent 工具调用 → Prompt 组装 → 流式生成」编排为条件路由状态图；Agent 通过 6 个工具查询用户真实数据作答；命中急症意图时强制注入「就医提示」安全护栏。
2. **高可用 SSE 流式对话链路**：自定义 `meta → delta → done` 事件协议 + 15s 心跳防掐断；全局信号量限流实现过载保护；用户中途停止生成时问题与部分回复双路收尾，避免内容丢失与记忆断裂。
3. **基于 LangGraph 官方 checkpoint 的可恢复对话记忆**：图状态 messages 按「用户 + 会话」线程自动持久化到 PostgreSQL，跨轮自动恢复上下文；被中断的轮次同样进入后续记忆。
4. **RAG 知识库双通路检索**：pgvector 语义检索（余弦相似度阈值 + 来源引用注入），Embedding 未配置或向量库故障时自动降级为字符 bigram 关键词检索。
5. **跨大模型厂商的结构化输出兼容**：`json_schema → function_calling → json_mode` 三级自动探测降级，规避不同服务商能力差异导致的「静默降级」。
6. **事件驱动的微服务基础设施**：RocketMQ 解耦异步链路（ES 索引同步、订单超时取消回补库存、点赞落库 + 通知、商家审核角色升级）；ES 故障 60s 熔断窗口自动降级；网关统一 JWT 鉴权与内部信任头注入，支撑跨语言身份透传。

更多细节见 [docs/秋招项目介绍.md](docs/秋招项目介绍.md) 与 [docs/后端开发规范.md](docs/后端开发规范.md)。

## 安全与密钥说明

- **本仓库不含任何真实密钥**：OSS AccessKey、JWT 密钥、数据库密码等均改为环境变量占位符；本地真实密钥放于各服务 `secrets-local.yml`（已忽略、不提交），运行时可正常加载。
- **AI 服务密钥**：真实 `LLM_API_KEY` / `EMBEDDING_API_KEY` 仅存于本地 `ai-service/.env`，该文件已被 `.gitignore` 忽略，仓库只提交 `.env.example` 模板。
- **生产部署务必**：
  1. 通过环境变量注入真实的 `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `JWT_SECRET`（所有服务 `JWT_SECRET` 保持一致）；
  2. 为 OSS 子账号配置最小权限（仅限目标桶读写）；
  3. 若仓库历史中曾提交过密钥，请立即到对应服务商控制台 **轮换（重新生成并作废旧密钥）**，并清理 Git 历史（如 `git filter-repo`）。
- `.gitignore` 已忽略 `secrets-local.yml`、`ai-service/.env` 等本地密钥文件，请勿删除忽略规则或使用 `git add -f` 强制提交。

## 常见问题

- **上传头像/图片报错「OSS 未配置」？** 当前服务的 `src/main/resources/secrets-local.yml` 不存在或未填密钥（克隆仓库后默认没有），补上后重启即可；也可用环境变量注入。
- **登录后 token 校验失败？** 确认所有服务（含 gateway）的 `JWT_SECRET` 完全一致。
- **搜索很慢或结果不准？** 未启动 Elasticsearch 时会自动降级为 MySQL 模糊查询，属预期行为；需要全文检索请启动 ES。
- **AI 对话返回 mock 回复？** `LLM_API_KEY` 为空或 `MOCK_CHAT=true` 时进入 mock 模式，配置真实 Key 即可。
- **修改 `petverse-common` 后其他服务没生效？** 需重新 `mvn -pl petverse-common install` 后再编译依赖服务。

## 许可证

本项目仅用于学习与交流用途。第三方组件遵循其各自的开源许可证。
