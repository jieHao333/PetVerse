# MCP 服务

`ai-service` 自带的 MCP（Model Context Protocol）服务。经「AI 扩展管理 → MCP 服务」注册启用后，由 `app/agent/extensions.py` 装配成 LangChain 工具交给模型按需调用。

## petcare_tools.py

养宠实用计算，全部为只读、无网络依赖、不读用户数据。

| 工具 | 用途 | 口径 |
|---|---|---|
| `pet_age_to_human` | 猫 / 狗年龄换算人类等效年龄 | 1 岁≈15、2 岁≈24，之后猫 +4/年、狗按体型 +4~7/年 |
| `daily_feeding_amount` | 每日所需能量与参考喂食克数 | RER = 70 × 体重^0.75，MER = RER × 生命阶段 / 绝育系数 |
| `daily_water_intake` | 每日参考饮水量区间 | 50~60 ml/kg/天 |
| `toxic_food_risk` | 有毒食物风险初筛 | 巧克力、葡萄、木糖醇、葱属、酒精咖啡因、夏威夷果，按体重与剂量分级 |

结果为兽医参考公式的估算，仅供养护参考，不替代诊断；`toxic_food_risk` 是初筛，疑似中毒应尽快联系宠物医院。

## 运行

依赖 `mcp` 包（安装 `requirements.txt` 中的 `langchain-mcp-adapters` 会一并带入），以 stdio 方式启动：

```bash
python mcp_servers/petcare_tools.py
```

## 注册

### 管理页

「AI 扩展管理 → MCP 服务 → 新增服务」：

| 字段 | 本地运行 | 容器内运行（ai-service 镜像） |
|---|---|---|
| 传输协议 | `Stdio（本地命令）` | 同左 |
| 启动命令 | Python 解释器绝对路径 | `python` |
| 命令参数 | `petcare_tools.py` 绝对路径 | `/app/mcp_servers/petcare_tools.py` |
| 启用 | 开 | 开 |

保存后点「测试」应列出 4 个工具，下一轮对话生效。

### 管理接口

需 ADMIN 身份（网关注入的 `X-User-Role`，或 `X-Kb-Admin` 令牌）：

```bash
curl -X POST http://localhost:8080/api/ai/admin/mcp/servers \
  -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"name":"养宠实用计算","transport":"stdio",
       "command":"python","args":["/app/mcp_servers/petcare_tools.py"],
       "description":"年龄换算 / 喂食量 / 饮水量 / 有毒食物风险初筛","enabled":true}'
```

命令与参数与部署环境相关，换机器或换镜像需重新填写。容器部署时必须用容器内路径，不能用宿主机路径。

## 配套 Skill

`../skills/petcare-calc/SKILL.md` 约定这四类问题的回答流程与安全口径：先补齐体重等输入，再调用工具，按规范呈现结果；中毒场景优先建议就医。MCP 负责「能算什么」，Skill 负责「什么时候算、怎么问、怎么答」，两者同时启用效果才完整。
