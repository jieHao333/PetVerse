# Nacos 配置中心接入教程

本项目所有 Java 微服务的业务配置（端口、数据库、Redis、OSS、JWT、RocketMQ、网关路由等）已统一迁移到 **Nacos 配置中心**，服务本地不再保存业务配置。本文介绍如何从零把配置导入 Nacos 并让服务正常启动。

> 适用版本：Spring Boot 3.4 / Spring Cloud 2024 / Spring Cloud Alibaba 2023.0.3.4 / Nacos 2.x。

---

## 1. 整体机制

```
服务启动
  └─ bootstrap.yml      声明：应用名、Nacos 地址、服务发现、file-extension
  └─ application.yml    仅一行导入声明：spring.config.import: nacos:{应用名}
        │
        ▼
  从 Nacos 拉取 dataId={应用名}（group=DEFAULT_GROUP，格式 YAML）
        │
        ▼
  得到端口 / 数据库 / OSS / JWT 等全部业务配置
```

关键约定：

| 项 | 值 | 说明 |
|---|---|---|
| dataId | `{应用名}`（**不带 `.yml` 后缀**） | 如 `gateway-service`、`shop-service` |
| group | `DEFAULT_GROUP` | 分组 |
| 命名空间 | `public`（默认，id 为空） | 可按需换自定义命名空间，见第 6 节 |
| 格式 | YAML | dataId 无后缀，客户端靠 `spring.cloud.nacos.config.file-extension=yml` 按 YAML 解析 |

各服务对应关系：

| 服务 | 端口 | dataId |
|---|---|---|
| gateway-service | 8080 | `gateway-service` |
| pet-service | 8081 | `pet-service` |
| space-service | 8082 | `space-service` |
| user-service | 8083 | `user-service` |
| social-service | 8084 | `social-service` |
| remark-service | 8085 | `remark-service` |
| shop-service | 8087 | `shop-service` |

---

## 2. 准备 Nacos 服务端（外置 MySQL 持久化）

默认 Nacos 用内嵌 Derby 存储，重启/迁移不便。生产与团队开发建议改用外置 MySQL 持久化。

### 2.1 建库建表

```sql
CREATE DATABASE nacos DEFAULT CHARACTER SET utf8mb4;
```

再导入 Nacos 发行包自带的表结构脚本：`{nacos}/conf/mysql-schema.sql`。

### 2.2 修改 `{nacos}/conf/application.properties`

```properties
# 新版 Nacos 推荐（旧版用 spring.datasource.platform=mysql）
spring.sql.init.platform=mysql
db.num=1
db.url.0=jdbc:mysql://127.0.0.1:3306/nacos?characterEncoding=utf8&connectTimeout=1000&socketTimeout=3000&autoReconnect=true&useUnicode=true&useSSL=false&serverTimezone=Asia/Shanghai
db.user.0=root
db.password.0=你的MySQL密码
```

### 2.3 ⚠️ 改完配置必须先停 Nacos 再重启

**这是最容易踩的坑**：如果在 Nacos **启动过程中**才修改数据源配置，本次进程仍会用启动那一刻的旧配置（内嵌 Derby）启动，导致你以为配了 MySQL、实际数据全进了 Derby，MySQL 里一条都没有。

判断当前用的是哪个库——查看 `{nacos}/logs/nacos-persistence.log`：

- `use StandaloneDatabaseOperateImpl` → 用的是 **内嵌 Derby**（持久化没生效）
- `[master-db] jdbc:mysql://...` → 用的是 **外置 MySQL**（正常）

若发现退回了 Derby：**完全停止 Nacos → 确认 application.properties 已保存 → 重新启动**，再重新推送配置（见第 4 节）。

---

## 3. 配置文件说明（`nacos-config/` 目录）

仓库 `PetVerse/nacos-config/` 下提供了 7 份可直接导入的配置模板：

```
nacos-config/
├── gateway-service.yml     # 文件名带 .yml 仅供本地编辑；导入时 dataId 取文件名去掉 .yml
├── pet-service.yml
├── space-service.yml
├── user-service.yml
├── social-service.yml
├── remark-service.yml
├── shop-service.yml
└── push-to-nacos.ps1       # 一键导入脚本
```

### 3.1 关于脱敏密钥（开源必读）

为便于开源，模板中所有**密钥类**字段均已用 `***********************` 脱敏，涉及：

- `aliyun.oss.access-key-id` / `aliyun.oss.access-key-secret`（阿里云 OSS 访问密钥）
- `jwt.secret`（JWT 签名密钥，网关与所有服务必须一致）

这些值的写法是 `${环境变量:默认值}`，例如：

```yaml
access-key-id: ${OSS_ACCESS_KEY_ID:***********************}
secret: ${JWT_SECRET:***********************}
```

含义：**运行时优先取同名环境变量，取不到才用冒号后的默认值**（此处默认值是脱敏占位）。因此你无需把真实密钥写进文件，只要给服务进程设置环境变量即可（见 3.2 方式一）。

> 数据库口令 `spring.datasource.password`（默认 `123456`）与 Redis 口令是本地开发默认值，非真实凭据，未脱敏；生产环境请务必修改。

### 3.2 三种填入真实密钥的方式（任选其一）

**方式一（推荐，开源友好）：给服务进程设置环境变量**

模板文件保持脱敏不动，在启动各 Java 服务的环境中设置：

```bash
# Linux / macOS
export OSS_ACCESS_KEY_ID=你的AccessKeyId
export OSS_ACCESS_KEY_SECRET=你的AccessKeySecret
export JWT_SECRET=一个足够长的随机字符串   # 网关与所有服务必须一致
```

```powershell
# Windows PowerShell
$env:OSS_ACCESS_KEY_ID="你的AccessKeyId"
$env:OSS_ACCESS_KEY_SECRET="你的AccessKeySecret"
$env:JWT_SECRET="一个足够长的随机字符串"
```

Spring 在读取 Nacos 下发的配置时，会用这些环境变量覆盖 `****` 占位。这样真实密钥永不进入代码仓库。

**方式二：直接在 Nacos 控制台修改**

先按第 4 节把模板导入 Nacos，再登录控制台（`http://localhost:8848/nacos`）逐个编辑 dataId，把 `****` 替换为真实值。真实密钥只存在 Nacos 服务端（MySQL），不进仓库。

**方式三：本地填好再推送（注意不要提交）**

把模板复制一份为 `*-local.yml`（该命名已被 `.gitignore` 忽略，不会提交），填入真实密钥后用脚本推送。**切勿把真实密钥写回会被提交的 `*-service.yml`**。

---

## 4. 推送配置到 Nacos

### 4.1 用脚本一键推送

确保 Nacos 已启动（`localhost:8848`），在项目根执行：

```powershell
powershell -ExecutionPolicy Bypass -File ./nacos-config/push-to-nacos.ps1
```

脚本会把 `nacos-config/*.yml` 逐个发布到 Nacos，**dataId 自动取文件名去掉 `.yml`**，`type=yaml`，`group=DEFAULT_GROUP`，命名空间默认 `public`。

支持的环境变量（可选）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `NACOS_ADDR` | `localhost:8848` | Nacos 地址 |
| `NACOS_GROUP` | `DEFAULT_GROUP` | 分组 |
| `NACOS_NAMESPACE` | 空（public） | 命名空间 id |

### 4.2 手动在控制台新建

登录 `http://localhost:8848/nacos` → 配置管理 → 配置列表 → 新建配置：

- **Data ID**：`gateway-service`（不带 `.yml`）
- **Group**：`DEFAULT_GROUP`
- **配置格式**：YAML
- **配置内容**：粘贴对应 `nacos-config/gateway-service.yml` 的内容

其余 6 个服务同理。

### 4.3 验证已落库

```sql
USE nacos;
SELECT data_id, group_id, type FROM config_info ORDER BY data_id;
-- 应看到 7 条：gateway-service / pet-service / space-service /
--            user-service / social-service / remark-service / shop-service
```

---

## 5. 客户端接入原理（服务侧无需额外操作）

每个服务本地只有两个配置文件：

**`bootstrap.yml`**（应用名 + Nacos 连接 + 服务发现 + 解析格式）：

```yaml
spring:
  application:
    name: shop-service
  cloud:
    nacos:
      server-addr: ${NACOS_SERVER_ADDR:localhost:8848}
      discovery:
        enabled: true
      config:
        enabled: true
        file-extension: yml      # dataId 无后缀时，据此把内容按 YAML 解析
        group: DEFAULT_GROUP
        refresh-enabled: true    # 配置变更自动刷新（配合 @RefreshScope）
```

**`application.yml`**（仅一行导入声明）：

```yaml
spring:
  config:
    import:
      - nacos:shop-service       # dataId，不带 .yml
```

> 为什么用 `spring.config.import` 而不是只靠 bootstrap？
> 在 Spring Cloud Alibaba 2023.0.3.4 + Spring Boot 3.4 下，**bootstrap 传统模式的配置定位器不再自动加载 Nacos 配置**，必须用 `spring.config.import: nacos:xxx` 显式导入，否则服务会因取不到配置而报 `Could not resolve placeholder 'jwt.secret'` 之类错误。

---

## 6. 使用自定义命名空间（可选）

默认用 `public`。若要隔离环境（如 dev/prod）：

1. 控制台 → 命名空间 → 新建命名空间，记下**命名空间 ID**（不是名称）。
2. 推送时指定：`$env:NACOS_NAMESPACE="<命名空间ID>"` 再执行 `push-to-nacos.ps1`。
3. 各服务 `bootstrap.yml` 的 `discovery` 与 `config` 下都加上 `namespace: <命名空间ID>`：

```yaml
spring:
  cloud:
    nacos:
      discovery:
        namespace: <命名空间ID>
      config:
        namespace: <命名空间ID>
```

---

## 7. 启动与验证

```bash
# 启动顺序：Nacos（+MySQL）→ 各业务服务 → 网关
java -jar gateway-service/target/gateway-service-0.0.1-SNAPSHOT.jar
```

启动日志出现以下三行即成功：

```
[Nacos Config] Load config[dataId=gateway-service, group=DEFAULT_GROUP] success
Netty started on port 8080 (http)
nacos registry, DEFAULT_GROUP gateway-service ... register finished
Started GatewayServiceApplication in X seconds
```

---

## 8. 常见问题（FAQ）

| 现象 | 原因 | 解决 |
|---|---|---|
| `Could not resolve placeholder 'jwt.secret'` | 配置没从 Nacos 加载：application.yml 缺 `spring.config.import`，或 Nacos 里没有对应 dataId | 检查 import 声明与 dataId 是否一致（不带 `.yml`）；确认已推送 |
| 配置改了但 MySQL 里查不到 / 重启就丢 | Nacos 退回内嵌 Derby（多半是启动中改的配置） | 见 2.3：停 Nacos → 确认配置 → 重启 → 重新推送 |
| dataId 无后缀，内容被当成 properties | 客户端未按 YAML 解析 | 确认 `bootstrap.yml` 里 `spring.cloud.nacos.config.file-extension=yml` |
| `Port 8080 was already in use` | 端口被上一个实例占用 | 结束占用进程，或改 Nacos 配置里的 `server.port` |
| OSS 上传报密钥无效 | 用了脱敏占位 `****` | 按 3.2 用环境变量/控制台填入真实 AccessKey |
| 令牌校验失败 / 频繁掉登录 | 各服务 `jwt.secret` 不一致 | 保证网关与所有服务用同一个 `JWT_SECRET` |

---

## 9. 开源安全清单

- ✅ `nacos-config/*.yml` 中密钥已脱敏为 `***********************`，可安全提交。
- ✅ 真实密钥通过**环境变量**或 **Nacos 控制台**注入，不写进仓库。
- ✅ 本地若保存填了真实密钥的副本，命名为 `*-local.yml`（已被 `.gitignore` 忽略）。
- ⚠️ 提交前用 `git grep "你的AccessKey前缀"` 自查，确认无真实密钥入库。
- ⚠️ 一旦真实密钥曾误提交，请立即到阿里云控制台**轮换（禁用并重建）AccessKey**。
