# PetVerse · 宠域
# Java + AI（LangGraph 生态）

面向宠物爱好者的社区平台后端，覆盖 **虚拟宠物养成 · 真实宠物档案 · 宠域空间（社区动态）· 好友与聊天 · 点赞评论通知 · 周边商城 · AI 养宠问询** 等业务域。

采用 **Java + Python 混合微服务**架构：Spring Cloud Gateway 统一入口，JWT 鉴权后向 Java / Python 两侧透传用户身份，各服务独立数据库、通过 Nacos 注册发现、RocketMQ 驱动异步链路。

| **PetVerse**（本仓库）| 8 个 Java 模块 + `ai-service`（Python） | Java 21 / Spring Boot 3.4 / Spring Cloud 2024 / FastAPI |
| **PetVerse-web**(https://github.com/jieHao333/PetVerse-web) | Web 前端 | Vue 3 + Vite + Element Plus，dev 端口 `5173`，`/api` 代理到网关 `8080` |

## 架构总览

```
浏览器 / 前端 (:5173)
      │
      ▼
Spring Cloud Gateway :8080 ── JWT 鉴权 · 剥离伪造身份头 · 注入 X-User-Id / X-User-Role
      │
      ├── /api/user/**    ──▶ user-service    :8083     MySQL petverse_user
      ├── /api/pet/**     ──▶ pet-service     :8081     MySQL petverse_pet
      ├── /api/space/**   ──▶ space-service   :8082     MySQL petverse_space
      ├── /api/social/**  ──▶ social-service  :8084     MySQL petverse_social
      ├── /api/remark/**  ──▶ remark-service  :8085     MySQL petverse_remark + Redis
      ├── /api/shop/**    ──▶ shop-service    :8087     MySQL petverse_shop
      └── /api/ai/**      ──▶ ai-service      :8086     PostgreSQL + pgvector + Redis
                                                  （FastAPI / LangGraph，见 ai-service/README.md）

共享基础设施：Nacos(8848 注册+配置) · Redis · RocketMQ(9876) · Elasticsearch(9200) · 阿里云 OSS · MySQL · PostgreSQL
```

前端只访问网关 `http://localhost:8080/api/**`，网关路由带 `StripPrefix=1`，业务服务内部路径不含 `/api` 前缀。

## 模块一览

| 模块 | 端口 | 数据存储 | 职责 |
|---|---|---|---|
| `gateway-service` | 8080 | — | 路由转发、JWT 鉴权、身份头注入、CORS、内部接口拦截与角色校验 |
| `user-service` | 8083 | MySQL `petverse_user` | 注册 / 登录（签发 JWT）、用户资料、角色（用户 / 商家 / 管理员）、头像上传 |
| `pet-service` | 8081 | MySQL `petverse_pet` + Redis(db1) | 虚拟宠物养成（等级 / 经验 / 每日签到 BitMap）、真实宠物档案与身份卡、品种图鉴、健康信息 |
| `space-service` | 8082 | MySQL `petverse_space` + ES | 宠域空间动态（图文 / 视频，三层可见性）、点赞评论聚合、热度榜、全文检索 |
| `social-service` | 8084 | MySQL `petverse_social` | 好友关系（申请 / 同意 / 拉黑）、私聊会话与消息、聊天附件上传 |
| `remark-service` | 8085 | MySQL + Redis(db2) | 面向多业务域的通用点赞 / 评论 / 站内通知；点赞走 Redis 缓冲 + 定时批量落库 |
| `shop-service` | 8087 | MySQL `petverse_shop` + ES | 商户入驻审核、店铺 / 商品管理、购物车、订单（延迟消息超时取消）、商品评价、全文检索 |
| `petverse-common` | — | — | **所有** DO / DTO / VO、统一返回与异常、JWT 上下文、MQ 发布与去重、OSS 封装、通用配置 |
| `ai-service` | 8086 | PostgreSQL `petverse_ai` + Redis(db3) | Python 服务：对话（SSE 流式 + RAG + 记忆）、健康评估、评论摘要、个性化推荐 |

## 环境准备

| 依赖 | 版本 | 本地默认 | 必须性 |
|---|---|---|---|
| JDK | 21 | — | ✅ 必须 |
| Maven | 3.9+ | — | ✅ 必须（仓库未带 mvnw，用本机 mvn） |
| Nacos | 2.x | `localhost:8848` | ✅ 必须（服务注册与发现） |
| MySQL | 8 | `localhost:3306`，root / 123456 | ✅ 必须（6 个业务库，见下） |
| Redis | 5+ | `localhost:6379`，密码 123456 | 点赞（db2）/ 签到 BitMap（db1）/ AI 缓存与并发计数（db3）需要 |
| PostgreSQL + pgvector | 含 `vector` 扩展 | `localhost:5432`，库 `petverse_ai` | AI 会话、对话记忆、RAG 需要 |
| RocketMQ | — | `localhost:9876` | 可选，异步链路（缺失自动降级） |
| Elasticsearch | 8.15.5 | `localhost:9200` | 可选，搜索（缺失降级 MySQL） |
| Python | 3.12 | — | 仅 ai-service |
| 阿里云 OSS | — | bucket `petverse-me` | 上传类功能需要 |
| Node.js | ^18 或 >=20 | — | 仅前端仓库 |

仓库不含中间件部署脚本，请自行准备（本地裸装或自建容器均可）。

## 快速开始

### 1. 初始化数据库

MySQL 建 6 个库（utf8mb4），再执行各服务自带的建表脚本：

```sql
CREATE DATABASE petverse_user   DEFAULT CHARACTER SET utf8mb4;
CREATE DATABASE petverse_pet    DEFAULT CHARACTER SET utf8mb4;
CREATE DATABASE petverse_space  DEFAULT CHARACTER SET utf8mb4;
CREATE DATABASE petverse_social DEFAULT CHARACTER SET utf8mb4;
CREATE DATABASE petverse_remark DEFAULT CHARACTER SET utf8mb4;
CREATE DATABASE petverse_shop   DEFAULT CHARACTER SET utf8mb4;
```

```bash
mysql -u root -p petverse_user   < user-service/src/main/resources/db/schema.sql
mysql -u root -p petverse_pet    < pet-service/src/main/resources/db/schema.sql
mysql -u root -p petverse_space  < space-service/src/main/resources/db/schema.sql
mysql -u root -p petverse_social < social-service/src/main/resources/db/schema.sql
mysql -u root -p petverse_remark < remark-service/src/main/resources/db/schema.sql
mysql -u root -p petverse_shop   < shop-service/src/main/resources/db/schema.sql
```

PostgreSQL（仅 AI 服务）：先 `CREATE DATABASE petverse_ai;`，再执行 `psql -U postgres -d petverse_ai -f ai-service/db/schema_pgvector.sql`（建扩展与表；会话 / 消息、checkpoint 表服务启动时也会幂等补建）。详见 [ai-service/README.md](ai-service/README.md)。

> 各服务 `db/schema.sql` 是表结构的唯一事实来源：改表结构时同步更新该文件，并把存量库迁移用的 `ALTER` 语句以注释形式补在同文件头部。

### 2. 配置密钥（OSS）

把 `secrets-local.yml` 放到需要的服务 `src/main/resources/` 下（已被 `.gitignore` 忽略，**禁止提交**）：

```yaml
local:
  oss:
    access-key-id: your-access-key-id
    access-key-secret: your-access-key-secret
```

也可用环境变量 `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` 覆盖（优先级更高）。没有 OSS 密钥时服务仍可启动，仅上传类功能不可用。

### 3. 启动中间件与服务

```bash
# 启动顺序：Nacos → MySQL / Redis / PostgreSQL（+ 可选 RocketMQ、Elasticsearch）→ 各微服务
mvn clean install -DskipTests          # 首次：构建全部模块（petverse-common 会被安装到本地仓库）

# 方式一：IDE 直接运行各模块 *Application 主类
# 方式二：命令行运行
java -jar user-service/target/user-service-0.0.1-SNAPSHOT.jar
java -jar pet-service/target/pet-service-0.0.1-SNAPSHOT.jar
# ……其余服务同理；网关最后启动（或先启动亦可，路由在请求时解析）
java -jar gateway-service/target/gateway-service-0.0.1-SNAPSHOT.jar
```

验证：`curl http://localhost:8080/api/user/...` 能返回统一报文即链路通畅。各服务也会尝试从 Nacos 读取同名配置（bootstrap.yml），本地 `application.yml` 已含全部默认值，**无需在 Nacos 建配置即可跑通**。

### 4. 启动 AI 服务与前端

- AI 服务：见 [ai-service/README.md](ai-service/README.md)（复制 `.env.example` 为 `.env`，`python -m uvicorn app.main:app --host 127.0.0.1 --port 8086`；未配 LLM Key 时 `MOCK_CHAT=true` 走内置 mock）。
- 健康探针：各服务与网关均暴露 `/actuator/health`（仅健康与信息端点，不经网关对外路由）。
- 前端（PetVerse-web 仓库）：`npm install && npm run dev`，访问 `http://localhost:5173`。

## 配置与环境变量

所有密钥类配置都提供「环境变量 > 本地密钥文件 > 开发默认值」的取值顺序，生产环境请用环境变量覆盖。

| 变量 | 作用范围 | 说明 |
|---|---|---|
| `MYSQL_URL` / `MYSQL_USERNAME` / `MYSQL_PASSWORD` | 各 Java 服务 | 库名不同，URL 默认值各自内置 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | remark-service | 点赞缓冲，db=2 |
| `JWT_SECRET` | 网关 + 所有 Java 服务 | **必须一致**，否则令牌校验失败；默认值仅供开发 |
| `JWT_EXPIRE_SECONDS` | user-service | 令牌有效期，默认 86400 秒 |
| `OSS_ENDPOINT` / `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_BUCKET_NAME` | user / pet / space / social / shop | 共用同一 bucket |
| `OSS_DOMAIN` | user-service / space-service | 自定义 / CDN 域名（含协议），为空时按桶默认域名拼接 |
| `petverse.search.enabled` | space-service / shop-service | 搜索总开关，`false` 时全部走 MySQL 模糊查询 |
| `petverse.order.pay-timeout-minutes` | shop-service | 支付超时分钟数，内部映射 RocketMQ 延迟档位 |
| `petverse.gateway.rate-limit.*` | gateway-service | 令牌桶限流开关与桶参数（普通接口 / AI 流式接口分别配置） |

AI 服务使用独立的 `.env` 配置（LLM / Embedding / PostgreSQL / 缓存 TTL 等），见 [ai-service/README.md](ai-service/README.md)。

## 关键约定（动手前必读）

1. **实体类集中在 `petverse-common`**：DO / DTO / VO 一律按业务域建子包放在公共模块，业务模块禁止新建实体类；命名与分层规则见 [docs/后端开发规范.md](docs/后端开发规范.md)。
2. **统一报文**：接口返回 `Result<T>` / `Result<PageResult<T>>`（`{code,msg,data}`），异常统一由 `GlobalExceptionHandler` 处理，业务模块不要自己拼错误响应。
3. **鉴权与身份透传**：`gateway-service` 的 `AuthGlobalFilter` 先剥离客户端自带的 `X-User-Id` / `X-User-Role` 防伪造，再校验 JWT 并重新注入；白名单 `auth.whitelist-paths`（默认登录 / 注册），`/api/shop/admin`、`/api/shop/merchant` 做角色校验，`/internal/**` 禁止从网关外部访问。下游服务通过 `UserContext` 取当前用户，Feign 调用由 `FeignUserContextConfig` 自动透传身份头 —— 业务代码不需要手写用户 ID 参数传递。
4. **服务间调用**：同步用 OpenFeign（各模块 `feign/` 包，被调方的服务间接口放 `/internal/` 路径；**列表聚合一律走批量接口**，如 `GET /user/internal/batch?ids=`，避免逐条调用）；异步用 RocketMQ，topic / tag 常量统一维护在 `petverse-common` 的 `MqTopics`，发布用 `MqEventPublisher`（**事务提交后才发送**，无 Broker 时降级为记日志），消费端自行保证幂等。
5. **表结构规范**：所有表继承 `BaseEntity` 约定 —— 雪花 `id`、`create_time` / `update_time` 自动填充、`deleted` 逻辑删除（MyBatis-Plus 全局生效，查询不必手写过滤）。雪花 ID 为 19 位 Long，已由 Jackson 统一序列化为字符串，避免前端精度截断 —— 前端传回的 ID 也是字符串。
6. **文件上传**：统一走 `petverse-common` 的 OSS 封装，各服务已配置各自的 `multipart` 大小上限（pet-service 头像 3MB、social-service 聊天文件 21MB、shop-service 单文件 51MB、space-service 视频 55MB，均含冗余），调整上限时同步改对应 `application.yml`。

## 中间件缺失时的行为

本地开发可以只起必须项，其余能力自动降级（详见各服务配置注释）：

| 未启动 | 影响 |
|---|---|
| Nacos / MySQL | 服务无法启动或注册失败 —— 必须启动 |
| Redis | remark-service 点赞不可用（点赞读写走 Redis，定时批量落库）；pet-service 签到改由宠物档案的最近签到日期判断（并发窗口内可能重复签到）；ai-service 结果缓存退化为重新计算、并发额度降级为进程内计数 |
| RocketMQ | 宠物经验发放、ES 索引同步、订单超时自动取消、商家角色升级等异步链路失效；主流程正常（发消息降级为记日志） |
| Elasticsearch | 动态 / 商品搜索降级为 MySQL `LIKE`（60s 熔断窗口后自动重试）；也可用 `petverse.search.enabled=false` 全局关闭 |
| PostgreSQL | ai-service 会话持久化与对话记忆降级、知识类提问明确报错 |
| OSS | 头像 / 动态媒体 / 商品图 / 聊天附件等上传功能不可用 |

## 新增一个功能的落地步骤

1. 改表：更新目标模块 `src/main/resources/db/schema.sql`（新表或 `ALTER` 注释），并在本地库执行。
2. 公共实体：在 `petverse-common` 对应业务域子包下新增 DO / DTO / VO（命名规范见开发规范文档）。
3. 业务实现：在目标服务按 `mapper → service → controller` 顺序补齐；Controller 只接收 DTO、只返回 `Result<VO>`，DO 不得越过 Service 暴露。
4. 跨服务数据：优先复用已有 Feign Client（`user / social / remark` 等），新增内部接口放 `/internal/`。
5. 异步链路：在 `MqTopics` 加常量，用 `MqEventPublisher` 发布，消费端做幂等。
6. 自测：仓库**暂无自动化测试模块**，请通过 `curl` / 前端联调手工验证正常与异常分支后再提交。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/后端开发规范.md](docs/后端开发规范.md) | 模块职责、包结构、命名、分层调用、表结构规范（新人必读） |
| [docs/AI模块功能说明.md](docs/AI模块功能说明.md) | AI 模块功能说明书：编排结构、接口契约、存储、配置、降级策略 |
| [ai-service/README.md](ai-service/README.md) | AI 服务启动、环境变量、接口列表、生产韧性设计 |
| [docs/秋招项目介绍.md](docs/秋招项目介绍.md) | 项目亮点与设计取舍（了解关键设计意图的来龙去脉） |

## 协作提醒

- **不要提交** `ai-service/.env`、`**/secrets-local.yml`：内含真实密钥，已在 `.gitignore` 中忽略；若曾误提交请立即轮换密钥。
- 新增环境变量时，记得给 `application.yml` 配一个 `localhost` 开发默认值，并在本 README 的变量表中登记。
- 改动公共模块（`petverse-common`）会影响全部服务，请本地构建全量模块通过后再提交。
