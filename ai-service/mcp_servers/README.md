# PetVerse MCP 服务

本目录存放项目自带、可直接接入对话 Agent 的 MCP（Model Context Protocol）服务。
它们经「AI 扩展管理」页（管理员）注册启用后，由 `app/agent/extensions.py` 装配成
LangChain 工具交给模型按需调用。

## petcare_tools.py — 养宠实用计算

为 AI 养宠顾问补充内置工具没有的**确定性养护计算**能力。全部为只读计算：无副作用、
无网络依赖、不读取用户数据，可安全交给模型调用。

| 工具 | 用途 | 口径 |
|---|---|---|
| `pet_age_to_human` | 猫 / 狗年龄换算人类等效年龄 | 1 岁≈15、2 岁≈24，之后猫 +4/年、狗按体型 +4~7/年 |
| `daily_feeding_amount` | 每日所需能量与参考喂食克数 | RER=70×体重^0.75，MER=RER×生命阶段/绝育系数 |
| `daily_water_intake` | 每日参考饮水量区间 | 50~60 ml/kg/天 |
| `toxic_food_risk` | 常见有毒食物风险初筛 | 巧克力/葡萄/木糖醇/葱属/酒精咖啡因/夏威夷果，按体重与剂量分级 |

> 计算结果均为常见兽医参考公式的估算，**仅供养护参考，不能替代兽医诊断**；
> `toxic_food_risk` 是初步初筛而非诊断，疑似中毒一律建议尽快联系宠物医院。

### 运行

依赖 `mcp` 包（安装 `requirements.txt` 里的 `langchain-mcp-adapters` 会一并带入）：

```powershell
python mcp_servers/petcare_tools.py        # 以 stdio 方式运行，等待 MCP 客户端连接
```

### 注册到 AI 扩展管理

在「AI 扩展管理 → MCP 服务 → 新增服务」中填写：

- **传输协议**：`Stdio（本地命令）`
- **启动命令**：装了 `mcp` 的 Python 解释器**绝对路径**（如本仓库使用的 conda 环境
  `D:\ProgramSoftware\miniconda3\envs\pet_verse\python.exe`）
- **命令参数**（每行一个）：本脚本的**绝对路径**，如
  `D:\Java\PetVerse_qiuzhao\PetVerse\ai-service\mcp_servers\petcare_tools.py`
- **启用**：开

保存后可点「测试」确认连通并看到 4 个工具；启用后下一轮对话即生效。

也可经管理接口注册（需 ADMIN 身份，网关或 `X-Kb-Admin` 令牌）：

```bash
curl -X POST http://localhost:8080/api/ai/admin/mcp/servers \
  -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"name":"养宠实用计算","transport":"stdio",
       "command":"<python 绝对路径>","args":["<petcare_tools.py 绝对路径>"],
       "description":"年龄换算/喂食量/饮水量/有毒食物风险初筛","enabled":true}'
```

> 命令与参数是**部署相关**的绝对路径，换机器需按实际解释器与脚本位置重新填写。