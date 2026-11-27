"""请求 / 响应数据契约，兼作喂给 LLM 的结构化输出模型。

两类模型同名同义（如 ``PetHealthReport`` 既是输出 schema 也是接口返回体），放一起避免漂移。

注意：字段上的 ``description`` 不只是文档——带 description 的模型会经 ``model_json_schema()``
注入提示词（见 ``app.llm._json_mode_hint``），改写这些文案会直接影响模型输出质量，勿随意精简。
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class PetInfo(BaseModel):
    """当前咨询的宠物档案（前端从 pet-service 取到后原样透传）。

    字段几乎全为可选：用户没建档也能直接开问，缺失时 Prompt 里对应行省略。
    ``health`` 是自由键值对（体重 / 疫苗 / 驱虫 / 病史），键名约定见 ``app.persona._HEALTH_LABELS``。
    """

    id: Optional[int] = None
    name: Optional[str] = None
    species: Optional[str] = None
    breed: Optional[str] = None
    age: Optional[int] = None
    ageText: Optional[str] = None
    level: Optional[int] = None
    signStreak: Optional[int] = None
    description: Optional[str] = None
    health: Optional[Dict[str, Optional[str]]] = None


class AttachmentInfo(BaseModel):
    """单条对话附件；当前只支持图片，``key`` 保留给服务端追溯 OSS 对象。"""

    type: str = Field(default="image", description="附件类型，当前仅支持 image")
    url: str = Field(description="附件地址（OSS 公网 URL）")
    key: Optional[str] = Field(default=None, description="OSS 对象 key")
    mime: str = Field(default="", description="MIME 类型")
    name: str = Field(default="", description="原始文件名")
    size: int = Field(default=0, description="文件大小（字节）")


class ChatRequest(BaseModel):
    """对话请求体；``message`` 与 ``attachments`` 至少有一个，校验在路由层做以返回业务错误码。"""

    message: str = Field(default="", description="用户本轮输入的文字消息；纯图片时可缺省")
    pet: PetInfo = Field(default_factory=PetInfo, description="当前咨询的宠物信息（缺省时不拼宠物档案）")
    sessionId: Optional[int] = Field(default=None, description="会话 ID；缺省时后端自动新建会话并通过 meta 事件回传")
    # 数量上限由 settings.CHAT_MAX_ATTACHMENTS 在路由层校验，这里不写死。
    attachments: List[AttachmentInfo] = Field(default_factory=list,
                                             description="本轮图片附件（仅图片），先经 /ai/chat/upload 上传")


class ApprovalRequest(BaseModel):
    """敏感操作的人工审批请求，由前端收到 SSE ``approval`` 帧后提交。

    批准则图从挂起点继续执行，拒绝则工具返回「未批准」，两者都会跑完这一轮对话。
    """

    sessionId: int = Field(description="会话 ID")
    approvalId: str = Field(default="", description="审批单 ID，取自 SSE approval 帧；用于防止旧审批被重放")
    approved: bool = Field(description="是否批准本次操作")
    reason: str = Field(default="", description="拒绝原因（可选），会转述给用户与模型，最多 200 字")


class SessionItem(BaseModel):
    """会话列表项；``petId`` 用字符串以与持久层返回格式保持一致。"""

    id: int
    petId: str = "0"
    title: str
    createTime: int = 0
    updateTime: int = 0


class SessionListData(BaseModel):
    """会话列表响应体。"""

    sessions: List[SessionItem] = []


class HistoryMessage(BaseModel):
    """历史消息项；比 LLM 侧 message 多出附件、引用与「被中止」标记，供前端还原。"""

    role: str
    content: str
    petId: Optional[str] = None
    ts: int = 0
    interrupted: bool = False
    attachments: List[AttachmentInfo] = Field(default_factory=list,
                                              description="该条消息携带的多模态附件（仅用户消息）")
    refs: List[Dict[str, Any]] = Field(default_factory=list,
                                       description="该条回答引用的知识库来源（仅 assistant 消息）："
                                                   "[{rank, docId, source, docTitle, kind, section, "
                                                   "category, score, matchedBy, snippet}]")


class HistoryData(BaseModel):
    """会话历史响应体（消息按时间正序）。"""

    sessionId: int
    messages: List[HistoryMessage] = []


def ok(data: Any = None) -> dict:
    """构造成功报文（全站 HTTP 状态码恒为 200，业务结果由 ``code`` 表达）。"""
    return {"code": 200, "msg": "success", "data": data}


def fail(code: int, msg: str) -> dict:
    """构造失败报文。

    ``msg`` 需为可直接展示的中文提示，不要把技术细节透给前端；
    错误码约定：401 未登录 / 400 参数错误 / 403 无权限 / 404 不存在 / 409 冲突 / 429 过载 / 500 异常。
    """
    return {"code": code, "msg": msg, "data": None}


class SearchQuery(BaseModel):
    """多轮追问改写结果，直接作为知识库检索的输入。"""

    query: str = Field(description="可直接用于知识库检索的完整问句：补全指代（它/那个/这种情况）"
                                   "与前文省略的主体，保留用户原意，不超过 40 字")
    # 只允许猫/狗：语料与物种过滤规则也只标注了这两类，放宽会让过滤失效。
    species: List[str] = Field(default_factory=list,
                               description="问题涉及的物种，只允许填「猫」或「狗」；同时涉及两者或不确定时留空")


class HealthReminder(BaseModel):
    """健康报告中的单条提醒。"""

    type: str = Field(description="提醒类型，如 疫苗 / 驱虫 / 体检 / 饮食 / 运动")
    advice: str = Field(description="具体建议")
    urgency: Literal["low", "medium", "high"] = Field(default="low", description="紧急程度")


class PetHealthReport(BaseModel):
    """健康评估报告；评分区间与等级语义在 health_graph 提示词中有对应约束，改这里须同步改提示词。"""

    score: int = Field(ge=0, le=100, description="综合健康评分，0-100")
    level: Literal["excellent", "good", "fair", "warning"] = Field(
        description="评级：excellent 优秀 / good 良好 / fair 一般 / warning 需关注")
    summary: str = Field(description="一句话总体评价")
    risks: List[str] = Field(default_factory=list, description="识别的健康风险点（无则空数组）")
    suggestions: List[str] = Field(default_factory=list, description="养护改进建议")
    care_plan: List[str] = Field(default_factory=list, description="近期养护计划（可执行事项）")
    reminders: List[HealthReminder] = Field(default_factory=list, description="疫苗/驱虫/体检等提醒")
    # 免责声明给默认值：LLM 漏输出该字段时，也不会把「未声明」的报告直接给用户。
    disclaimer: str = Field(default="本评估基于用户填写信息由 AI 生成，不能替代兽医面诊，如有异常请及时就医。",
                            description="免责声明")


class ReviewSummary(BaseModel):
    """商品评论摘要（口碑概览）。"""

    sentiment: Literal["positive", "neutral", "negative"] = Field(description="整体情感倾向")
    one_line: str = Field(description="一句话总结")
    pros: List[str] = Field(default_factory=list, description="优点列表（来自真实评论）")
    cons: List[str] = Field(default_factory=list, description="缺点列表（来自真实评论）")
    keywords: List[str] = Field(default_factory=list, description="高频关键词")
    count: int = Field(default=0, description="参与总结的评论条数")


class RecommendItem(BaseModel):
    """单条推荐结果。"""

    id: int = Field(description="候选内容 ID（商品或动态，取自候选列表）")
    type: Literal["product", "space"] = Field(description="推荐类型：product 商品 / space 动态")
    reason: str = Field(description="推荐理由，结合用户画像，不超过 30 字")
    score: float = Field(default=0.5, description="推荐相关度 0-1")


class RecommendResult(BaseModel):
    """推荐结果集合，items 需按相关度降序。"""

    items: List[RecommendItem] = Field(default_factory=list, description="按相关度降序的推荐列表")
    summary: str = Field(default="", description="整体推荐说明")


# 记忆归类：preference 偏好 / habit 习性 / fact 客观事实，仅用于展示与提示词组织，不参与检索过滤。
MemoryKind = Literal["preference", "habit", "fact"]


class MemoryItem(BaseModel):
    """长期记忆条目（对外展示形态）。"""

    key: str
    scope: Literal["user", "pet"] = Field(description="记忆归属：user 用户级 / pet 宠物级")
    petId: Optional[str] = None
    petName: Optional[str] = None
    kind: MemoryKind = "preference"
    content: str
    createdAt: int = 0
    updatedAt: int = 0


class MemoryListData(BaseModel):
    """长期记忆列表响应体。"""

    memories: List[MemoryItem] = []


class MemoryOp(BaseModel):
    """一轮对话触发的单条记忆维护操作（增 / 改 / 删 / 无操作）。"""

    action: Literal["add", "update", "delete", "none"] = Field(
        description="add 新增记忆 / update 更新既有记忆（必须带 key）/ delete 删除过时或错误的既有记忆（必须带 key）/ none 本轮无长期记忆变更")
    key: Optional[str] = Field(default=None, description="update / delete 时对应的既有记忆 key；add / none 时留空")
    scope: Literal["user", "pet"] = Field(default="user", description="记忆归属：与具体宠物无关的用户信息用 user；当前宠物的习性/偏好/病史用 pet")
    content: str = Field(default="", description="记忆内容，50 字以内的一条客观事实；action 为 none/delete 时留空")
    kind: MemoryKind = Field(default="preference", description="记忆归类：preference 偏好 / habit 习性 / fact 事实")


class MemoryUpdateResult(BaseModel):
    """LLM 输出的记忆维护操作列表。

    让模型输出操作指令而非直接写库：每条可校验（key 是否存在、content 是否为空），
    失败也不会污染已有记忆。
    """

    ops: List[MemoryOp] = Field(default_factory=list, description="本轮需要执行的记忆维护操作；无长期有效信息时为空列表或仅一条 none")


class McpServerUpsert(BaseModel):
    """MCP 服务配置的新增 / 更新请求体（管理员接口）。

    传输协议三选一：``streamable_http`` / ``sse`` 填 ``url``（可带 ``headers``），
    ``stdio`` 填 ``command`` + ``args``（可带 ``env``）；组合约束在路由层校验。
    """

    name: str = Field(min_length=1, max_length=64, description="服务名称（唯一，管理页与日志展示用）")
    transport: Literal["streamable_http", "sse", "stdio"] = Field(
        default="streamable_http", description="MCP 传输协议")
    url: str = Field(default="", max_length=500, description="streamable_http / sse 的服务地址")
    command: str = Field(default="", max_length=500, description="stdio 模式的启动命令（如 npx / uvx / python）")
    args: List[str] = Field(default_factory=list, description="stdio 模式的命令参数列表")
    env: Dict[str, str] = Field(default_factory=dict, description="stdio 模式注入子进程的环境变量")
    headers: Dict[str, str] = Field(default_factory=dict, description="HTTP 模式的自定义请求头（如 Authorization）")
    description: str = Field(default="", max_length=255, description="服务用途说明（仅管理页展示）")
    enabled: bool = Field(default=True, description="是否启用（停用后模型立即看不见该服务的工具）")


class SkillUpsert(BaseModel):
    """Skill 技能包的新增 / 更新请求体（管理员接口，标准 Agent Skills 范式）。

    落地为 ``skills/<name>/SKILL.md``：``name`` + ``description`` 写入 YAML frontmatter，
    ``content`` 是 Markdown 正文；``description`` 是技能选择的唯一依据，须单行说明「做什么 + 何时用」。
    """

    name: str = Field(min_length=1, max_length=64, description="技能名（hyphen-case，如 petcare-calc），同时是目录名与唯一标识")
    description: str = Field(min_length=1, max_length=1024, description="单行说明：做什么 + 何时触发（技能选择的唯一依据）")
    content: str = Field(min_length=1, max_length=20000, description="SKILL.md 正文（Markdown 指令，不超过 500 行）")
    enabled: bool = Field(default=True, description="是否启用（停用后下一轮对话不再出现在技能目录）")


class ModelItem(BaseModel):
    """供应商下的一个模型（「Provider + Model」两级结构里的 Model 层）。

    同一供应商的模型共享协议 / Base URL / Key，只有模型 ID 与能力不同；
    因此切换模型只需要改一个指针，不必重复填凭证。
    """

    id: str = Field(min_length=1, max_length=128,
                    description="模型 ID（服务商要求的 model 名，如 deepseek-chat / claude-sonnet-4-5）")
    label: str = Field(default="", max_length=64, description="管理页展示名；留空则显示模型 ID")
    vision: bool = Field(default=False, description="该模型是否支持图片输入（支持时图片会直接送给它）")
    maxTokens: int = Field(default=0, ge=0, le=1048576,
                           description="单次生成的最大输出（token）；0 表示沿用全局 LLM_MAX_TOKENS。"
                                       "管理页支持 128K / 1M 写法，提交前已换算成 token（1K=1024，1M=1048576）")


class ModelProviderUpsert(BaseModel):
    """模型供应商的新增 / 更新请求体（管理员接口）。

    ``protocol`` 决定 Base URL 与 SDK：``openai`` 走 ``/chat/completions``（地址一般带 ``/v1``），
    ``anthropic`` 走 ``/v1/messages``（地址一般不带 ``/v1``）。更新时 ``apiKey`` 留空表示沿用原 Key，
    避免管理页把脱敏后的掩码当成新 Key 提交。
    """

    name: str = Field(min_length=1, max_length=64, description="供应商名称（唯一，管理页展示用）")
    protocol: Literal["openai", "anthropic"] = Field(
        default="openai", description="协议：openai = OpenAI 兼容；anthropic = Anthropic Messages")
    baseUrl: str = Field(default="", max_length=500, description="接口地址（不带 /chat/completions 等具体路径）")
    apiKey: str = Field(default="", max_length=500, description="API Key；更新时留空表示不修改")
    models: List[ModelItem] = Field(default_factory=list, description="该供应商下可用的模型清单")
    description: str = Field(default="", max_length=255, description="用途说明（仅管理页展示）")
    enabled: bool = Field(default=True, description="是否启用（停用后不能再被选为当前模型）")


class ModelActivateRequest(BaseModel):
    """切换当前生效模型。"""

    providerId: int = Field(description="供应商 ID")
    model: str = Field(min_length=1, max_length=128, description="模型 ID（须在该供应商的模型清单内）")


class ModelTestRequest(BaseModel):
    """模型连通性测试请求。"""

    providerId: int = Field(description="供应商 ID")
    model: str = Field(min_length=1, max_length=128, description="要测试的模型 ID")
