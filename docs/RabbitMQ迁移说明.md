# RocketMQ → RabbitMQ 迁移说明

本文件记录「把所有 RocketMQ 用法替换为 RabbitMQ」的梳理结果、映射规则、行为一致性对照与改动清单，供评审与后续维护使用。

## 1. 迁移前的 RocketMQ 使用位置

| 类别 | 位置 |
|---|---|
| 依赖 | 根 `pom.xml`（`rocketmq-spring.version` + `dependencyManagement`）、`petverse-common/pom.xml`、`PetVerseSingle/pom.xml` |
| 契约与基础设施 | `common/mq/MqTopics`（topic/tag 常量）、`MqEventPublisher`（发送器 + 事务后发送 + 降级）、`MqDeduplicator`（消费端幂等） |
| 生产者 | `SpaceServiceImpl`、`ProductServiceImpl`、`MerchantServiceImpl`、`MerchantApplyServiceImpl`、`OrderServiceImpl`、`UserCouponServiceImpl`（单体）、`LikeServiceImpl`、`CommentServiceImpl`、`ProductReviewServiceImpl`、`LikeNotifyConsumer`（消费中转发） |
| 消费者（微服务 10 个） | `SpaceExpConsumer`、`MerchantRoleConsumer`、`MerchantWithdrawalConsumer`、`ProductIndexConsumer`、`OrderTimeoutConsumer`、`NotificationConsumer`、`SpaceIndexConsumer`、`LikeNotifyConsumer`、`LikeCountConsumer`、`CouponReceiveConsumer` |
| 消费者（单体 9 个） | 同上，去掉 promotion 侧独立服务（优惠券消费者位于 `shop/mq`） |
| 配置 | `nacos-config/{pet,user,shop,space,remark,promotion}-service.yml`、单体 `application.yml` / `application-server.yml` |
| 文档 | 两份 README、`docs/Nacos配置中心接入教程.md`、两份项目经历文档 |

## 2. 映射规则

| RocketMQ | RabbitMQ | 说明 |
|---|---|---|
| topic | topic 类型 Exchange | 常量值不变，沿用原 topic 名 |
| tag | RoutingKey | 消费端按需绑定一个或多个 RoutingKey，等价 `tagA \|\| tagB` |
| consumerGroup | 队列名 | 一个队列 = 原来的一份消费，组间互不影响 |
| `RocketMQTemplate.syncSend` | `RabbitTemplate.send` + 发布确认 | 等 Broker ack，并校验 mandatory 退回 |
| `RocketMQHeaders.KEYS` | `messageId` + `mq-key` 头 | 消费端 `MqMessages.key()` 统一读取 |
| `MessageExt#getTags()` | `receivedRoutingKey` | `MqMessages.tag()` 统一读取 |
| `maxReconsumeTimes` | 容器重试 `maxAttempts` | 默认 16，通知/点赞类 5 |
| 重试耗尽 → `%DLQ%group` | 拒绝不重回队列 → `petverse.dlx` → `petverse.dlq` | 保留消息体与 `x-death` |
| `delayLevel`（固定 18 档） | TTL 队列 + 死信交换机 | 档位与毫秒映射沿用原表，业务侧入参不变 |
| `rocketmq.name-server` 缺失即降级 | `petverse.mq.enabled=false` / Broker 不可达 | 发送返回 false，触发本地兜底 |

## 3. 行为一致性对照

| 关注点 | 迁移后行为 |
|---|---|
| 序列化 | 仍为 JSON（UTF-8）；生产端 `ObjectMapper.writeValueAsBytes`，消费端 `readValue`，解析失败记日志后正常消费（不重试），与原实现一致 |
| 发送时机 | `publishAfterCommit` 仍注册 `TransactionSynchronization#afterCommit`；无事务时立即发送 |
| 发送失败 | 不抛异常，返回 false；5 参重载执行调用方传入的本地 fallback（单体降级路径不变） |
| 投递可靠性 | 消息持久化 + publisher confirm + mandatory 退回校验，只有真正入队才算成功 |
| 消费失败 | 抛异常即重试（1s 起、2 倍退避、上限 10s），次数对齐原 `maxReconsumeTimes`；业务异常（如 `BusinessException`）仍被捕获后不再重试 |
| 幂等 | `MqDeduplicator` 逻辑未变：失败先 `release(key)` 再抛出，保证重试不被去重键拦截 |
| 延迟消息 | 先入 TTL 队列，到期经死信交换机按目标 RoutingKey 回到目标 Exchange，消费端与普通消息完全一致 |
| 开关 | `petverse.mq.enabled=false` 时不创建消费者、发送直接返回 false；`isEnabled()` 语义保持「基础设施是否装配」 |

## 4. 改动清单

**依赖**：根 `pom.xml`、`petverse-common/pom.xml`、`PetVerseSingle/pom.xml` —— 移除 `rocketmq-spring-boot-starter`，引入 `spring-boot-starter-amqp` 与 `spring-retry`（显式声明，版本由 Boot 统一管理）。

**新增（两份工程各一套，包名一致）**：

- `common/mq/MqQueues.java`：队列名（= 原消费组）、死信交换机/队列、延迟队列命名规则
- `common/mq/MqMessages.java`：JSON 编解码、messageId/key、RoutingKey 读取
- `common/mq/RabbitMqConfig.java`：死信兜底、listener 容器工厂（并发/预取/重试/拒绝策略）、mandatory
- `common/mq/RabbitMqDelaySupport.java`：TTL + 死信交换机实现延迟消息

**改造**：`MqTopics`（改为 Exchange/RoutingKey 契约，常量值不变）、`MqEventPublisher`（RabbitTemplate + 发布确认）、`MqDeduplicator`（注释）、19 个消费者（`@RabbitListener` + `@QueueBinding` + `@ConditionalOnProperty`）、6 份 Nacos 配置、单体 2 份配置、两份 README 与 Nacos 教程、两份项目经历文档。

## 5. 上线前需人工确认

1. **Broker 就绪**：部署 RabbitMQ 3.x，创建 vhost / 账号（默认配置为 `guest/guest`，仅适用于本机），确认 5672 与 15672 端口策略。
2. **配置下发**：6 份 `nacos-config/*.yml` 需重新导入 Nacos 后重启服务，否则 `spring.rabbitmq.*` 不生效。
3. **总开关**：单体 `application.yml` 的 `petverse.mq.enabled` 默认仍为 `false`（沿用原部署习惯），服务器部署 RabbitMQ 后需显式置为 `true` 或注入 `MQ_ENABLED=true`。
4. **延迟时长口径**：当前仍按固定档位向上取整（如配 15 分钟实际 20 分钟），如需精确到配置值，可改为按分钟动态声明 TTL 队列——属于行为变更，需产品确认。
5. **容量参数**：容器并发 4~16、prefetch 10 为经验值，需按实际消息量与单条处理耗时压测调整。
6. **死信运维**：`petverse.dlq` 需纳入监控与重放流程（原 `%DLQ%` 的等价物），重放时注意消费端幂等键。
7. **存量消息**：切换后旧 RocketMQ 中未消费完的消息不会迁移，需确认切换窗口内无在途订单超时等关键事件。
8. **文档口径**：两份项目经历文档已把 RocketMQ 改为 RabbitMQ，涉及个人叙述的措辞请自行复核。
9. **端到端验证**：本次仅完成编译级验证（两套工程 `mvn clean compile` 通过），消息收发、延迟到期、重试与死信需在有 Broker 的环境联调。
