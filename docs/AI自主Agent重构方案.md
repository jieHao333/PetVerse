# AI 对话模块：从硬编码编排图到自主 AgentLoop

> 分支：`youhua-AgentLoop`　模块：`ai-service`　范围：`/ai/chat/**` 对话链路

本文记录「养宠顾问对话」从**固定状态图编排**改造为**模型自主决策的 AgentLoop** 的调研结论、
方案设计与实现约束。健康评估 / 评论摘要 / 个性化推荐三张图属于「确定性批处理」，不在此次
改造范围内，继续保留 `StateGraph` 编排。

---

## 1. 调研结论

### 1.1 一个事实标准：AgentLoop = 模型 ⇄ 工具

调研了 GitHub 上 6 个成熟实现，循环结构高度一致：

```
准备上下文 → 调模型 → 模型返回 tool_calls？
                        ├─ 是 → 执行工具 → 结果写回消息 → 再调模型
                        └─ 否 → 该条消息即最终答案 → 结束
```

差别只在**终止控制**、**扩展点**与**是否把循环暴露给业务代码**：

| 项目 | 循环实现 | 终止条件 | 扩展点 | 对本项目的适配度 |
|---|---|---|---|---|
| **LangChain 1.0 `create_agent`**（langchain-ai/langchain） | LangGraph 编译出的 `model` / `tools` 两节点图 | 最后一条 AI 消息无 `tool_calls`；全部工具 `return_direct`；结构化输出完成 | **Middleware**（`before_model` / `wrap_model_call` / `wrap_tool_call` / `after_model`），钩子既是节点也是装饰链 | ★★★★★ 原生 LangGraph，checkpoint / store / 流式直接复用 |
| **LangGraph `create_react_agent`**（langgraph prebuilt） | 同上，1.0 已让位给 `create_agent`，`ToolNode` 保留并被 `create_agent` 内部复用 | `tools_condition` 判断有无 `tool_calls` | 自定义节点、`state_schema`、`ToolNode(wrap_tool_call=...)` | ★★★★★ `ToolNode` 可直接复用 |
| **OpenAI Agents SDK**（openai/openai-agents-python） | `Runner.run()` 内 `while` 循环：调模型 → handoff / tool_calls / 最终输出 | 无工具调用的文本输出；`max_turns` 超限抛 `MaxTurnsExceeded`，可用 `error_handlers` 转成兜底答复 | `Agent`（instructions/tools/handoffs/guardrails）、`RunConfig` | ★★★☆☆ 循环语义值得抄，但不在 LangChain 生态 |
| **smolagents**（huggingface/smolagents） | `MultiStepAgent` 单文件 ReAct 循环（`agents.py` < 1000 行） | 调用 `final_answer` 工具；`max_steps` 上限 | `Tool` 抽象、`planning_interval`、CodeAgent/ToolCallingAgent | ★★☆☆☆ 极简可读，适合理解循环本质，不适合接入现有 LangGraph 记忆 |
| **deepagents**（langchain-ai/deepagents） | 在 `create_agent` 上叠 middleware：planning（`write_todos`）、filesystem、subagents、summarization | 同 `create_agent` | Middleware 组合 + Backend 协议 | ★★★☆☆ 思路可借鉴（规划工具、子 agent 隔离上下文），当前对话场景暂不需要 |
| **Anthropic《Building effective agents》** | 论文/工程指南，非代码 | — | — | ★★★★★ 架构判断依据 |

**Anthropic 的核心判断**：workflow（预定义代码路径）与 agent（模型自主决定流程）是两类系统，
应「先用最简单的方案，只有在确实改善结果时才增加复杂度」；自主 agent 适用于**步骤数无法预先
确定**的开放式任务，且必须配 **最大迭代次数** 与 **护栏**。

### 1.2 选型结论

**采用 LangGraph 自建 3 节点循环，复用 `ToolNode` 作为工具执行器，拓扑与 `create_agent` 保持同构。**

不直接调用 `create_agent`，是因为本项目有 4 条 middleware 无法干净覆盖的硬约束：

1. **引用来源（refs）必须在答案首字之前以 SSE 帧下发并随消息落库**，且编号 `[1] [2]` 要与
   知识库片段全局一致。`create_agent` 的 `wrap_tool_call` 可以拦截，但引用还需要参与状态持久化
   与多轮编号偏移，自己持有 `act` 节点最直接。
2. **只能流式输出「最终答案」那一轮的 token**。`create_agent` 的模型节点统一叫 `model`，
   工具规划轮的 token 与答案轮同节点，只能靠 `tool_call_chunks` 事后过滤；自建节点可在
   条件边上明确区分「还会调工具」与「这是答案」。
3. **知识检索失败必须整轮明确报错**（`RagUnavailable` 直接向上抛），而不是让模型拿着
   「检索失败」的 ToolMessage 继续编答案。这要求工具执行错误不被 ToolNode 吞掉，且路由层
   能收到原始异常。
4. **状态要与存量 checkpoint 兼容**：已有会话的 `messages` 通道必须原样加载，同时新增
   `knowledge` / `step` / `medical` 等通道。自建 `state_schema` 比在 `AgentState` 上叠
   middleware 的 `state_schema` 更可控。

复用的部分：`langgraph.prebuilt.ToolNode`（并行工具派发 + 参数错误修复）、`langchain_core.tools`
（`@tool` / `InjectedState` / `response_format="content_and_artifact"`）、`ChatOpenAI.bind_tools`、
`PostgresSaver` / `PostgresStore`（对话记忆与长期记忆）。

### 1.3 借鉴清单

| 来源 | 借鉴点 | 落地位置 |
|---|---|---|
| `create_agent` | 循环拓扑（model → tools → 条件边）、`Send` 并行工具派发 | `app/agent/graph.py` |
| `create_agent` | middleware 的「每步可插拔」思想 | `app/agent/hooks.py`（节点内钩子函数） |
| OpenAI Agents SDK | `max_turns` + 超限兜底答复（`error_handlers`） | `AGENT_MAX_STEPS` + `conclude` 节点 |
| deepagents | 工具描述即 ACI（给模型写清适用场景与空结果含义） | `app/agent/tools/*.py` docstring |
| Anthropic | 自主循环必须配迭代上限与确定性护栏 | `app/agent/policy.py` |

---

## 2. 现状与问题

改造前 `app/graph/chat_graph.py` 是一张 6 节点固定图：

```
START → classify_intent ─┬─(Send)→ retrieve_knowledge ─┐
                         ├─(Send)→ tool_action ────────┼→ recall_memory → compose → generate → END
                         └────────(无命中)─────────────┘
```

问题不在「用了图」，而在**决策被写死在代码里**：

| 问题 | 具体表现 |
|---|---|
| 意图表是硬编码的 | `_KB_INTENTS` / `_TOOL_WORDS` / `_URGENT_WORDS` 三张词表决定走向；新增一类问题就要改词表 + 改路由 + 改 state |
| 意图分类是独立 LLM 调用 | 每轮多一次模型往返；分类错了整轮都错（「猫藓用什么药，我上次买的还有吗」同时是知识与订单，只能二选一或都跑） |
| 工具子 agent 与主图割裂 | `tool_action` 内部再跑一次 `create_agent`，工具结果被压成一段文本塞进 Prompt，中间的工具调用轨迹全部丢失，无法排障 |
| 上下文无条件预注入 | 检索结果不管模型用不用都进 Prompt，长片段挤占窗口，也诱导模型「为了引用而引用」 |
| 扩展成本 | 加一个「查疫苗记录」工具 = 改 `tools.py` + 改 `tool_query` 判定 + 改 persona 文案 + 可能改 state 字段 |

---

## 3. 目标架构

### 3.1 分层

```
┌─────────────────────────────────────────────────────────────┐
│ 接入层  app/chat.py                                          │
│ SSE 协议（meta/refs/delta/done/error）· 会话 · 并发闸 · 落库   │
└───────────────────────────┬─────────────────────────────────┘
                            │  graph.astream(state, config)
┌───────────────────────────▼─────────────────────────────────┐
│ 运行时层  app/agent/graph.py                                 │
│   prepare ──▶ agent ◀──▶ act        （模型自主循环）          │
│                 └──▶ conclude / finalize                     │
└───────────────────────────┬─────────────────────────────────┘
                            │  bind_tools / ToolNode
┌───────────────────────────▼─────────────────────────────────┐
│ 能力层  app/agent/tools/                                     │
│   knowledge（RAG+引用） · pet · shop · community · profile    │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│ 基础层  llm · kb · clients · checkpoint · memstore · persona │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 核心模块划分

| 文件 | 职责 | 关键点 |
|---|---|---|
| `app/agent/state.py` | 状态定义与初始值 | `messages` 用 `add_messages`（兼容旧 checkpoint）；`knowledge` 用去重合并 reducer |
| `app/agent/graph.py` | 图装配与编译 | 单例缓存；`prepare / agent / act / conclude / finalize` 五节点 + 条件边 |
| `app/agent/prompt.py` | Agent 版 System Prompt | 人设 + 工具策略（ACI）+ 引用规则 + 医疗护栏 + 档案/记忆 |
| `app/agent/policy.py` | 确定性护栏与上下文工程 | 医疗关键词、知识类判定、历史窗口裁剪（保留 tool_call 组完整性）、中断调用补齐 |
| `app/agent/tools/registry.py` | 工具注册表 | 分组装配（`AGENT_TOOL_GROUPS`），按用户/宠物闭包注入身份 |
| `app/agent/tools/knowledge.py` | 知识检索工具 | 多轮改写 + 混合检索 + 全局引用编号 + `artifact` 回传引用 |
| `app/agent/tools/business.py` | 业务查询工具 | 迁移原 `app/tools.py` 的 7 个工具，签名与文档不变 |
| `app/agent/tools/actions.py` | 写操作工具 | 加购 / 点赞 / 评论 / 档案 / 下单支付；身份来自 `InjectedState`，不来自参数 |
| `app/agent/idempotency.py` | 写操作幂等 | 同轮同参数只真正执行一次，覆盖模型重复调用与路由层重试 |
| `app/agent/approval.py` | 人工审批（HITL） | 敏感工具用 `interrupt()` 挂起整图，审批后才继续；无 checkpointer 时失败关闭 |
| `app/agent/events.py` | 流事件适配 | 从节点更新中提取 refs，供路由层转 SSE |

### 3.3 状态通道

| 通道 | 类型 | Reducer | 说明 |
|---|---|---|---|
| `messages` | `list[AnyMessage]` | `add_messages` | 全程消息流：用户 → AI(tool_calls) → Tool → AI(答案) |
| `query` | `str` | last | 本轮原文，用于日志与 mock 引用 |
| `pet` | `dict` | last | 宠物档案（工具与 Prompt 共用） |
| `user_id` | `int` | last | 数据隔离，工具闭包捕获 |
| `attachments` | `list` | last | 附件；有图时进入视觉单轮模式 |
| `system_prompt` | `str` | last | `prepare` 产出，`agent` 每轮复用 |
| `medical` | `bool` | last | 医疗护栏是否生效 |
| `needs_evidence` | `bool` | last | 知识类判定，仅用于强化提示词 |
| `step` | `int` | last | 已执行工具轮次，用于预算控制 |
| `knowledge` | `list[dict]` | `merge_knowledge` | 引用来源，按 `chunk_id` 去重，编号全局递增 |
| `reply` | `str` | last | 最终回答，供路由层落库 |
| `vision` | `bool` | last | 是否多模态只读轮次 |

### 3.4 AgentLoop 决策流程

```
                    ┌──────────────────────┐
                    │ prepare（确定性）     │
                    │ 档案 · 记忆 · 护栏    │
                    │ 系统提示 · 视觉预检索 │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
        ┌──────────▶│ agent（LLM）         │
        │           │ bind_tools → 决策     │
        │           └──────────┬───────────┘
        │                      │ should_act
        │        ┌─────────────┴──────────────┐
        │        │ 有 tool_calls 且未超预算     │ 无 tool_calls
        │        ▼                            │
        │  ┌──────────────────────┐           │
        └──│ act（ToolNode）       │           │
   step+1  │ 并行执行 · 采集引用    │           │
           └──────────┬───────────┘           │
                      │ 达到 AGENT_MAX_STEPS  │
                      ▼                       ▼
           ┌──────────────────────┐  ┌──────────────────────┐
           │ conclude（无工具）     │  │ finalize             │
           │ 强制收敛 · 给最终答复   │  │ 提取 reply · 兜底     │
           └──────────┬───────────┘  └──────────┬───────────┘
                      └──────────┬──────────────┘
                                 ▼
                                END
```

伪代码（即 `agent` / `should_act` 的真实逻辑）：

```python
async def agent(state):
    model = vision_model if state["vision"] else chat_model
    bound = model if state["vision"] else model.bind_tools(tools)
    resp = await bound.ainvoke([SystemMessage(state["system_prompt"]), *trimmed(state["messages"])])
    return {"messages": [resp]}                       # 工具轮或答案轮都由模型自己决定

def should_act(state):
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return "finalize"                             # 模型认为信息够了
    if state["step"] >= settings.AGENT_MAX_STEPS:
        return "conclude"                             # 预算耗尽，强制收敛
    return "act"                                      # 继续调工具
```

### 3.5 决策权边界

「把决策权交给模型」不等于放弃工程约束。本方案把职责切成两半：

| 由模型决定（自主） | 由代码保证（确定性） |
|---|---|
| 要不要检索知识库、检索几次 | 每轮最多 `AGENT_MAX_STEPS` 次工具调用 |
| 要不要查订单/购物车/口碑，先查哪个 | 工具只能查当前登录用户的数据（闭包注入 `user_id`） |
| 一次问句同时涉及多类数据时并行还是串行 | 工具结果的 token 上限与列表裁剪 |
| 信息够了就停止，不必凑满轮次 | 医疗/急症关键词 → 必附「及时就医」护栏 |
| 引用哪几条、怎么组织答案 | 引用编号由服务端分配，模型只能标注 |

### 3.6 工具（ACI）设计

工具描述遵循「能力 + 适用场景 + 空结果含义」三段式，这是模型能否正确选工具的主要变量
（Anthropic 称之为 ACI，与 UI 设计同等重要）。

| 工具 | 组 | 用途 |
|---|---|---|
| `search_pet_knowledge` | knowledge | 养宠知识/症状/喂养/疫苗等，返回带 `[n]` 编号的片段 |
| `get_my_pets` | pet | 我的宠物档案 |
| `get_my_orders` | shop | 我的订单与自提码 |
| `get_my_cart` | shop | 我的购物车 |
| `search_products` | shop | 商城在售商品检索 |
| `get_product_reviews` | shop | 指定商品口碑 |
| `get_hot_posts` | community | 社区热门动态 |
| `get_my_profile` | profile | 养护画像与偏好（数据中台） |

以上为只读能力。写操作（加购、点赞、评论、发布、档案维护、下单支付）的清单、分级与
安全边界见第 7 节。

---

### 3.7 办事与咨询：两种回答范式

同一句「帮小黑买肠胃敏感的猫粮」，用户要的是「买到」而不是「科普」。所以提示词先判场景：

| 场景 | 主线 | 引用 | 典型失败 |
|---|---|---|---|
| 办事（买 / 加购 / 点赞 / 发布 / 改档案） | 先给结果或可执行选项，再补一句必要提醒 | 不需要 | 写一篇带 `[1][2]` 的医嘱，什么都没做 |
| 咨询（症状 / 怎么选 / 能不能吃） | 知识依据 + 引用 | 需要 | 答得太泛，没有出处 |

配套还有一处判定修正：`policy.classify_hints` 原来只认「买到 / 买什么」，不认「买 / 下单 / 加购」，
于是把购物请求判成知识咨询，又在提示词里追加「先检索知识库」，进一步把模型推向 RAG。
现在购物动作词也算「办事」，不再追加检索提示。

购物场景的三条硬规则：关键词太窄要换宽再搜；没有完全匹配就给 1~2 个可买替代并问用户；
加购前先看购物车——加购是累加语义，重复加会变成两件。

## 4. 兼容性设计

| 维度 | 改造前 | 改造后 | 是否兼容 |
|---|---|---|---|
| SSE 事件 | `meta → refs → delta* → done` | 不变；敏感操作时追加 `approval` 并结束本次流 | ✅ 兼容旧事件，新增一种 |
| 敏感操作 | 无 | `interrupt` 挂起 + 人工审批 + `POST /ai/chat/approve` 恢复 | ⚠️ 新增协议，前端需接审批卡片 |
| 引用来源 | 检索节点产出 | `act` 节点产出，`refs` 帧时机不变（答案首字前） | ✅ |
| 对话记忆 | `thread_id = 用户:会话`，`messages` 通道 | 不变，旧 checkpoint 可直接加载 | ✅ |
| 长期记忆 | `prepare` 召回用户级 + 宠物级 | 不变 | ✅ |
| 中断补写 | `aupdate_state(as_node="generate")` | 改为 `as_node="finalize"`（新图节点名） | ⚠️ 需同步改路由 |
| Mock 模式 | 跑图取 refs + 模拟流 | 直接检索取 refs + 模拟流（不再跑图） | ✅ 输出一致 |
| 医疗护栏 | 意图命中 → Prompt 追加就医规则 | 关键词命中 → Prompt 追加同一规则 | ✅ 文案不变 |
| 检索失败 | `RagUnavailable` → error 帧 | 工具执行期异常向上抛 → 同一 error 帧 | ✅ |
| 检索触发 | 知识意图**必定**检索 | 模型判断是否检索 | ⚠️ 行为差异，靠提示词 + 评测回归约束 |
| 多模态 | 视觉模型 + 预注入检索结果 | 视觉轮次不绑工具，`prepare` 预检索 | ✅ |

唯一需要产品侧确认的差异：**模型可能选择不检索而直接回答常识问题**。这在语义上是正确的
（「你好」不该触发检索），但需要通过评测集确认知识类问题的检索率没有下降。

---

## 5. 扩展指南

新增一项业务能力（例如「查疫苗记录」）：

1. 在 `app/agent/tools/business.py` 写一个 `@tool`，docstring 按三段式描述；
2. 在 `registry.py` 的组表里登记（或新增一个组，并加进 `AGENT_TOOL_GROUPS`）；
3. 需要特殊提示时，在 `prompt.py` 的工具策略段补一句。

不需要改图、不需要改条件边、不需要改路由层。工具的增删只影响模型看到的工具清单。

---

## 6. 风险与验证

| 风险 | 缓解 |
|---|---|
| 模型该检索却不检索 | 工具描述写清「凡是养宠知识/症状/用药问题都必须先检索」；`needs_evidence` 命中时在提示词里再强化一次；用 `scripts/eval_rag.py` 的黄金集回归检索率 |
| 工具轮次膨胀导致成本上升 | `AGENT_MAX_STEPS=3`；超限走 `conclude` 无工具收敛；工具结果 2000 字符上限 |
| 模型把工具名/内部字段说给用户 | Prompt 明确禁止暴露工具与实现细节 |
| 工具并行调用导致下游压力 | 工具本身已有超时（`HTTP_TIMEOUT=8s`）与失败降级；`ToolNode` 并行度受模型单轮 tool_calls 数约束 |
| 用户中止留下「有调用无结果」的半截历史 | `policy.repair_dangling_tool_calls` 在构造模型输入时补齐（幂等），避免下一轮 400 |
| 旧 checkpoint 反序列化异常 | 新 state 保留 `messages` 通道且 reducer 不变；已用旧 schema 写入的 checkpoint 做跨版本回放验证 |

### 6.1 已完成的验证

离线（脚本化假模型，不访问 LLM）：

| 用例 | 结果 |
|---|---|
| 知识问题 → 模型调用 `search_pet_knowledge` → 引用 [1][2] → 作答 | 通过：artifact 采集 2 条、编号连续、消息序列 `Human/AI/Tool/AI` 合法 |
| 寒暄 → 零工具调用、直接作答 | 通过：`step=0`、`knowledge=[]` |
| 预算耗尽（`AGENT_MAX_STEPS=1`）→ 拒绝第二次调用 → 收敛 | 通过：为未执行的 `tool_call_id` 补了说明性 ToolMessage，最终答复正常 |
| 检索不可用 → `RagUnavailable` | 通过：异常穿出图，路由层可转 `error` 帧 |
| 旧 schema checkpoint（含 `intents`/`knowledge_text`）→ 新图续聊 | 通过：旧历史进入模型输入，新状态写回同一 `thread_id` |
| 流式事件 | 通过：`refs` 帧先于 `finalize`；`is_answer_chunk` 只放行 `agent`/`conclude` 的正文 |

真实模型联调（DeepSeek，项目 `.env`）：

| 输入 | 观察到的行为 |
|---|---|
| 「幼猫一般什么时候开始打疫苗？需要注意什么」 | 模型**并行**调用知识检索（带 `species=猫`）与宠物档案查询；5 条引用，答案按 `[1]~[5]` 标注 |
| 「你好呀」 | 零工具调用，直接寒暄 |
| 「我家猫最近老是呕吐，需要去医院吗」 | 检索 + 分诊建议，`medical=true`（护栏生效） |

一次联调暴露的 ACI 问题已修复：模型会重复调用 `get_my_pets` 去查已经写在系统提示里的档案。
现在能力清单里明确写了「基础档案已给出，除非需要档案外的记录，否则不必重复查询」。

流式链路单独验证：同一问题用 `stream_mode=["updates","messages"]` 消费，先收到 1 批
`refs`（5 条），随后 720 个 `delta` 片段全部来自 `agent` 节点——证明「引用先于正文、
只下发答案轮 token」在真实模型下成立。

### 6.2 待回归项

- [ ] `scripts/eval_rag.py` 黄金集：确认知识类问题的检索触发率与 hit@5 没有下降
- [ ] 前端联调：`refs` 面板、`done`/`error` 帧、停止生成后的历史回放
- [ ] 存量会话抽查：随机取若干老 `thread_id` 继续对话，确认无 400 / 无重复历史
---

## 7. 写操作与能力边界

只读工具让回答「有依据」，写操作让对话「能办事」。但**能办事**不等于**什么都能办**：
AI 该替代用户做「表达与选择」，不该替代用户做「授权与付款」。

### 7.1 工具清单与分级

| 分组 | 工具 | 下游接口 | 默认 | 风险判断 |
|---|---|---|---|---|
| `cart` | `add_to_cart` / `update_cart_quantity` / `remove_from_cart` | `POST`·`PUT /shop/cart`、`DELETE /shop/cart/{id}` | **开** | 可撤销（重新加购即可） |
| `interaction` | `like_post` / `unlike_post` | `POST`·`DELETE /remark/like/0/{id}` | **开** | 幂等、可撤销 |
| `pet_edit` | `register_pet` / `update_pet_health` | `POST /pet/register`、`PUT /pet/health` | **开** | 仅影响本人档案 |
| `content` | `comment_post` / `publish_post` | `POST /remark/comment`、`POST /space` | 关 | 公开发布，影响他人可见内容 |

**刻意不在清单里的**：下单、支付、取消订单。理由见 7.3。

### 7.2 数据可见性：普通用户能看到什么，工具就只给什么

商品检索是典型例子：`/shop/product/page` 只过滤「上架 + 店铺营业」，**不保证有库存**，
且会回传 `merchantId`、`shopStatus`、撤销状态等内部字段。工具层做三件事：

1. **可见性再收口**：只保留 `status=1`、`stock>0`、店铺营业且不在撤销冷静期的商品；
   任何一条不满足都不进模型上下文——避免 AI 推荐一个用户根本买不了的东西，到加购时才失败。
2. **字段投影**：商品只给 `id/name/price/stock/shopName/categoryName/imageUrl/description(80字)`，
   去掉 `merchantId` 与店铺内部状态；社区动态只给
   `id/title/content/category/authorNickname/likeCount/createTime`，去掉 `userId`/`visibility`/媒体列表。
3. **身份随请求走**：所有下游调用注入 `X-User-Id`，业务服务再做一次归属校验；模型无法通过参数
   指定「查谁的数据」。

原则：**AI 的可见范围 = 当前登录用户在 App 里的可见范围**，不多一个字段，不少一个能力。

### 7.3 交易类动作：先拿掉能力，再谈审批

下单 / 支付 / 取消订单**不开放给模型**，而不是「开放 + 人工审批」。原因：

- 用户说「帮我买」，语义上授权的是「挑商品、加购物车」，不是「替我付钱」；
- 即使有审批，模型仍可能把「下单」理解成「支付授权」，而真实付款意愿只有用户自己清楚；
- 不给工具是硬边界，审批是软边界——能用前者就不用后者。

所以 AI 的电商闭环是：**检索 → 加购 → 引导用户去 App 购物车结算**。用户说「帮我下单 / 付款」时，
模型会说明「商品已在购物车，请在购物车确认订单并支付」，不会声称已下单或已付款。

### 7.4 保留下来的写操作怎么防

公开发布（`publish_post`）属于「必须由 AI 发起、但要人把关」的动作，用 Human-in-the-loop：

1. **可见性（配置层）**：`content` 分组默认关闭，模型看不见这些工具。
2. **人工审批（工具层）**：写库前 `interrupt()` 挂起整图，前端弹卡片，用户批准后才继续。
   **批准动作发生在一次独立的 HTTP 请求里，由用户触发，模型无法自己「点同意」。**
3. **幂等（运行时层）**：`run_id` + 工具名 + 参数指纹，同轮同参数只执行一次。加购是累加语义，
   而模型可能一条回复发两次调用、路由层「首帧前重试」也会重跑图。
   注意这是**进程内**缓存，多 worker 下重试落到别的进程仍会重放一次。
4. **失败关闭**：审批依赖 checkpointer 保存中断现场；数据库不可用时直接拒绝执行，不退化成自动放行。

### 7.5 审批交互协议

```
用户：「把今天带豆豆打疫苗的事发个动态」
  ↓  POST /ai/chat/stream
SSE: meta → delta* → approval {approvalId, action:"publish_post",
                               summary:[{可见性},{内容}]}
      （本次流结束，没有 done；图停在 act 节点，checkpoint 保存现场）
  ↓  前端弹卡片，用户点「同意」/「拒绝」
POST /ai/chat/approve {sessionId, approvalId, approved, reason}
  ↓  校验：会话归属 → 是否存在待审批中断 → approvalId 是否匹配
SSE: meta → delta* → done      （批准：先发动态，再由模型给出结果）
```

实现要点：

- **审批单一次性**：恢复后中断即被消费，重复调用返回「没有待审批的操作」；
  `approvalId` 必须匹配当前中断 id，防止拿旧审批批准新操作。
- **挂起前只读**：`interrupt()` 恢复时所在节点**从头重跑**，所以挂起前不能有副作用。
- **待审批期间发新消息**：LangGraph 会丢弃挂起任务按新输入继续，历史里留下「有 tool_calls
  无结果」的半截调用——由 `policy.repair_dangling_tool_calls` 在下次构造模型输入时补齐。
- **一次只审批一个**：提示词要求需要审批的操作单独调用，不要与其他工具并发。

### 7.6 刻意不做的

| 不做 | 原因 |
|---|---|
| 下单 / 支付 / 取消订单 | 交易闭环属于用户授权，AI 只负责把商品放进购物车 |
| 删除动态 / 删除评论 / 删除宠物 | 不可逆，对话里误触发的收益远小于风险 |
| 带图片/视频的发布 | 需要先走 `/space/media` 上传，对话链路没有媒体上传能力 |

### 7.7 已验证的行为

**可见性（假下游）**：

- 下架、无库存、店铺打烊/封禁/撤销冷静期的商品被过滤，不进入模型上下文
- 商品/动态字段被投影，`merchantId`、`userId`、`visibility` 等不再出现在工具结果里

**写操作（11 个用例）**：

- 加购成功回执商品名与数量；同轮重复调用只发一次 HTTP；跨轮次正常重复执行
- 业务失败原样透出「库存不足」且**不写**幂等缓存
- 改量走 `PUT` body `{id, quantity}`；移除走 `DELETE /shop/cart/{id}`
- 点赞/取消点赞的 `targetType=0` 与 `POST`/`DELETE` 路径
- 评论默认不走审批；健康档案回执中文化
- 未登录直接拒绝；无审批通道时公开发布**失败关闭**

**人工审批（LangGraph `interrupt` + `Command(resume)`，7 个用例）**：

- 发布挂起：产出审批事件、卡片含可见性与内容、**未产生写请求**、挂起前不输出答案
- 图停在 `act` 节点；批准后恢复才发出 `POST /space`
- 审批单只能用一次；拒绝后零副作用且 ToolMessage 写明原因
- 无 checkpointer 时失败关闭；非敏感动作（加购）不触发审批
- 真实 PostgresSaver 下完整跑通挂起 → 审批 → 恢复
