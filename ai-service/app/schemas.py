"""请求 / 响应数据契约。

职责：集中定义对外 HTTP 接口的请求体、响应体，以及「喂给 LLM 的结构化输出模型」。
两类模型放在一起是刻意为之——它们大多同名同义（如 ``PetHealthReport`` 既是 LLM 的输出
schema，也是接口返回体），分开维护必然漂移。

主要导出：
    - 接口层：``ChatRequest`` / ``AttachmentInfo`` / ``SessionListData`` / ``HistoryData``
    - 通用报文：``ok`` / ``fail``（全站统一 HTTP 200 + ``{code,msg,data}``）
    - 结构化输出：``IntentResult`` / ``SearchQuery`` / ``PetHealthReport`` / ``ReviewSummary``
      / ``RecommendResult`` / ``MemoryUpdateResult``
    - 共享枚举：``IntentType`` / ``MemoryKind``

外部依赖：仅 ``pydantic``。

使用示例：
    from app.schemas import PetInfo, ok, fail

    return JSONResponse(status_code=200, content=ok({"sessionId": 12}))
    return JSONResponse(status_code=200, content=fail(401, "未登录"))

注意：字段上的 ``description`` 不只是文档——带 description 的模型会经
``model_json_schema()`` 注入提示词（见 ``app.llm._json_mode_hint``），
改写这些文案会直接影响模型输出质量，勿随意精简。
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class PetInfo(BaseModel):
    """当前咨询的宠物档案（前端从 pet-service 取到后原样透传）。

    字段几乎全为可选：用户可能还没建档就直接开问，缺失时 Prompt 里对应行直接省略，
    不阻塞对话。``health`` 是自由结构的键值对（体重 / 疫苗 / 驱虫 / 病史等），
    键名约定见 ``app.persona._HEALTH_LABELS``。
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
    """单条多模态附件。

    先经 ``POST /ai/chat/upload`` 上传并拿到 OSS 地址，再随对话请求回传；
    ``key`` 用于服务端二次读取（ASR 转写 / 视觉理解），因此即使已有 ``url`` 也要保留。
    """

    type: Literal["image", "audio", "video"] = Field(description="附件类型")
    url: str = Field(description="附件地址（OSS 公网 URL）")
    key: Optional[str] = Field(default=None, description="OSS 对象 key")
    mime: str = Field(default="", description="MIME 类型")
    name: str = Field(default="", description="原始文件名")
    size: int = Field(default=0, description="文件大小（字节）")
    transcript: Optional[str] = Field(default=None, description="音频转写文本（后端转写后回填）")


class ChatRequest(BaseModel):
    """对话请求体。

    ``message`` 与 ``attachments`` 至少要有一个（只发图片/语音也允许），
    校验在路由层完成而非模型层——这样能返回业务错误码而不是 422。
    """

    message: str = Field(default="", description="用户本轮输入的文字消息；纯附件时可缺省")
    pet: PetInfo = Field(default_factory=PetInfo, description="当前咨询的宠物信息（缺省时不拼宠物档案）")
    sessionId: Optional[int] = Field(default=None, description="会话 ID；缺省时后端自动新建会话并通过 meta 事件回传")
    # 数量上限由 settings.CHAT_MAX_ATTACHMENTS 在路由层校验，这里不写死。
    attachments: List[AttachmentInfo] = Field(default_factory=list,
                                             description="本轮多模态附件（图片/音频/视频），先经 /ai/chat/upload 上传")


class SessionItem(BaseModel):
    """会话列表项；``petId`` 用字符串而非整数，与服务端持久层返回的格式保持一致。"""

    id: int
    petId: str = "0"
    title: str
    createTime: int = 0
    updateTime: int = 0


class SessionListData(BaseModel):
    """会话列表响应体。"""

    sessions: List[SessionItem] = []


class HistoryMessage(BaseModel):
    """历史消息项。

    比 LLM 侧的 message 多出 ``attachments`` / ``refs`` / ``interrupted``：前端要靠它们
    还原附件气泡、引用来源面板，以及「这轮被用户中止」的标记。
    """

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
    """构造成功报文。

    全站约定 HTTP 状态码恒为 200，业务结果由 ``code`` 表达，前端只解析这一层。

    Args:
        data: 业务数据，允许为 None（如删除类接口）。

    Returns:
        ``{"code": 200, "msg": "success", "data": data}``。

    Raises:
        无。
    """
    return {"code": 200, "msg": "success", "data": data}


def fail(code: int, msg: str) -> dict:
    """构造失败报文。

    Args:
        code: 业务错误码（401 未登录 / 400 参数错误 / 403 无权限 / 404 不存在 /
            409 冲突 / 429 过载 / 500 服务异常）。
        msg: 可直接展示给用户的中文提示，避免把技术细节透给前端。

    Returns:
        ``{"code": code, "msg": msg, "data": None}``。

    Raises:
        无。
    """
    return {"code": code, "msg": msg, "data": None}


# 意图枚举与 chat_graph 的提示词、路由判断共用一套取值，新增取值需同步
# _KB_INTENTS / _route_after_classify，否则新增意图不会触发任何分支。
IntentType = Literal["chitchat", "knowledge", "health", "medical_urgent", "tool_query"]


class IntentResult(BaseModel):
    """多意图识别结果（可为多选）。"""

    intents: List[IntentType] = Field(
        description="用户本轮问题命中的全部意图，可多选；命中多个时全部返回，至少返回一个")
    reason: str = Field(default="", description="简要说明分类依据")


class SearchQuery(BaseModel):
    """多轮追问改写结果，直接作为知识库检索的输入。"""

    query: str = Field(description="可直接用于知识库检索的完整问句：补全指代（它/那个/这种情况）"
                                   "与前文省略的主体，保留用户原意，不超过 40 字")
    # 只允许猫/狗：语料与物种过滤规则目前也只标注了这两类，放宽会让过滤失效。
    species: List[str] = Field(default_factory=list,
                               description="问题涉及的物种，只允许填「猫」或「狗」；同时涉及两者或不确定时留空")


class HealthReminder(BaseModel):
    """健康报告中的单条提醒。"""

    type: str = Field(description="提醒类型，如 疫苗 / 驱虫 / 体检 / 饮食 / 运动")
    advice: str = Field(description="具体建议")
    urgency: Literal["low", "medium", "high"] = Field(default="low", description="紧急程度")


class PetHealthReport(BaseModel):
    """健康评估报告：LLM 结构化输出模型，同时作为接口返回体。

    评分区间与等级语义在 health_graph 的提示词中有对应约束，改这里要同步改提示词，
    否则模型产出的 level 与 score 会自相矛盾。
    """

    score: int = Field(ge=0, le=100, description="综合健康评分，0-100")
    level: Literal["excellent", "good", "fair", "warning"] = Field(
        description="评级：excellent 优秀 / good 良好 / fair 一般 / warning 需关注")
    summary: str = Field(description="一句话总体评价")
    risks: List[str] = Field(default_factory=list, description="识别的健康风险点（无则空数组）")
    suggestions: List[str] = Field(default_factory=list, description="养护改进建议")
    care_plan: List[str] = Field(default_factory=list, description="近期养护计划（可执行事项）")
    reminders: List[HealthReminder] = Field(default_factory=list, description="疫苗/驱虫/体检等提醒")
    # 免责声明默认值兜底：LLM 偶尔漏输出该字段时，也不会把「未声明」的报告直接给用户。
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


# 记忆归类：preference 偏好 / habit 习性 / fact 客观事实，仅用于展示与提示词组织，
# 不参与检索过滤。
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

    用「让模型输出操作指令」而不是「让模型直接写库」：操作可被逐条校验（key 是否存在、
    content 是否为空），失败也不会污染已有记忆。
    """

    ops: List[MemoryOp] = Field(default_factory=list, description="本轮需要执行的记忆维护操作；无长期有效信息时为空列表或仅一条 none")
