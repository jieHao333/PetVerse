# Elasticsearch 搭建指南（Windows 本地开发）

本文档用于 PetVerse 项目的搜索改造（宠域空间动态搜索、宠物商城商品搜索）。

## 1. 版本选择

| 组件 | 版本 | 说明 |
|---|---|---|
| Elasticsearch | **8.15.5** | 服务端 |
| analysis-ik（IK 中文分词） | **8.15.5** | 插件版本必须与 ES 版本完全一致，否则拒绝加载 |

### 为什么是 8.15.5

项目使用 Spring Boot 3.4.13，其依赖清单 `spring-boot-dependencies-3.4.13.pom` 中锁定了：

```
<elasticsearch-client.version>8.15.5</elasticsearch-client.version>
```

即 Spring Boot 3.4.13 自带的 ES Java 客户端为 8.15.5。服务端选用同版本可保证客户端与服务端协议完全对齐，
无需在项目中覆写任何依赖版本号。

## 2. 下载地址

Elasticsearch 8.15.5（Windows zip，约 431 MB）：

```
https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.15.5-windows-x86_64.zip
```

SHA512 校验文件（可选）：

```
https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.15.5-windows-x86_64.zip.sha512
```

IK 分词插件 8.15.5（约 4.4 MB）：

```
https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.15.5.zip
```

> IK 插件自 8.x 起不再发布到 GitHub Releases，官方下载站为 https://release.infinilabs.com 。

## 3. 安装步骤

### 3.1 解压 Elasticsearch

将 zip 解压到**不含中文和空格**的路径，例如：

```
D:\dev\elasticsearch-8.15.5
```

ES 8.x 自带 JDK（位于 `jdk` 目录），无需单独安装 Java。若本机 `JAVA_HOME` 指向了其他版本导致启动异常，
可设置环境变量 `ES_JAVA_HOME` 指向 ES 自带的 `jdk` 目录，或临时清空 `JAVA_HOME`。

### 3.2 安装 IK 插件

在 ES 的 `plugins` 目录下新建 `analysis-ik` 目录，把 IK 的 zip **解压后的文件**放进去（不是把 zip 直接放进去）：

```
D:\dev\elasticsearch-8.15.5\plugins\analysis-ik\
    elasticsearch-analysis-ik-8.15.5.jar
    plugin-descriptor.properties
    config\...（词典文件）
    ...
```

也可以用插件命令行安装（需联网，会自动匹配当前 ES 版本）：

```powershell
cd D:\dev\elasticsearch-8.15.5
bin\elasticsearch-plugin install https://get.infini.cloud/elasticsearch/analysis-ik/8.15.5
```

### 3.3 关闭安全认证（仅本地开发）

编辑 `config\elasticsearch.yml`，追加/修改以下配置，使 ES 以 HTTP 明文、免认证方式提供服务，
与项目配置 `spring.elasticsearch.uris: http://localhost:9200` 匹配：

```yaml
cluster.name: petverse-es
node.name: node-1
network.host: 127.0.0.1
http.port: 9200
discovery.type: single-node

# 本地开发关闭安全特性；生产环境请勿如此配置
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
```

### 3.4 调整堆内存（可选）

默认堆可能偏大。编辑 `config\jvm.options`，本地开发建议 1GB：

```
-Xms1g
-Xmx1g
```

### 3.5 启动

```powershell
cd D:\dev\elasticsearch-8.15.5
bin\elasticsearch.bat
```

首次启动约需 30~60 秒。保持该窗口不关闭即为运行状态。

## 4. 验证

### 4.1 验证 ES 已启动

```powershell
curl http://localhost:9200
```

正常返回 JSON，其中 `version.number` 应为 `8.15.5`。

### 4.2 验证 IK 插件已加载

```powershell
curl http://localhost:9200/_cat/plugins?v
```

应能看到 `analysis-ik  8.15.5`。

### 4.3 验证中文分词效果

```powershell
curl -X POST "http://localhost:9200/_analyze" -H "Content-Type: application/json" -d "{\"analyzer\":\"ik_max_word\",\"text\":\"金毛幼犬狗粮推荐\"}"
```

应返回「金毛」「幼犬」「狗粮」「推荐」等词条，而不是逐字切分。

## 5. 项目侧配置

`space-service` 与 `shop-service` 的 `application.yml` 中已加入：

```yaml
spring:
  elasticsearch:
    uris: http://localhost:9200
    connection-timeout: 2s
    socket-timeout: 5s

petverse:
  search:
    enabled: true
```

- `petverse.search.enabled: false` 可强制关闭 ES 搜索，全部回退到 MySQL LIKE 查询。
- ES 未启动时服务仍能正常运行：索引初始化失败只记日志，搜索自动降级为 MySQL LIKE。
- 服务启动时若索引不存在，会自动建索引（含 IK 分词映射）并把库中已有数据全量灌入。

## 6. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 启动报 `plugin [analysis-ik] ... was built for Elasticsearch version x.x.x` | IK 版本与 ES 版本不一致，必须都是 8.15.5 |
| 访问 9200 要求账号密码或跳 HTTPS | `xpack.security.*` 未关闭，见 3.3 |
| 启动闪退无日志 | 解压路径含中文/空格，或 `JAVA_HOME` 冲突，见 3.1 |
| 搜索结果为空但数据库有数据 | 索引未灌入，重启 space-service / shop-service 触发全量重建 |
