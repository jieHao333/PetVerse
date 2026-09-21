"""数据模型定义

与 Java 端 petverse-common 的 Result / DTO 结构对齐，
保证前后端拿到统一的 {"code":200,"msg":"success","data":...} 报文。

除对外请求/响应模型外，还包含供 LLM 结构化输出使用的模型
（意图识别、健康评估报告、评论摘要、推荐结果），
这些模型同时作为 with_structured_output 的 schema，字段描述会传给模型。
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class PetInfo(BaseModel):
    """宠物信息（咨询上下文输入）

    字段全部可选容错：前端少传 / 漏传任何字段都不影响对话主流程，
    咨询上下文构建时会省略缺失的档案行。
    """

    id: Optional[int] = None          # 宠物 ID（会话按用户 + 宠物隔离的依据）
    name: Optional[str] = None        # 宠物名字
    species: Optional[str] = None     # 物种（猫 / 狗 / ...）
    breed: Optional[str] = None       # 品种
    age: Optional[int] = None         # 年龄（数值：真实宠物为按生日换算的整岁数；虚拟宠物为年龄字段值）
    ageText: Optional[str] = None     # 年龄精确文本（真实宠物按生日换算，含月龄如「8 个月」「1 岁 3 个月」；渲染档案时优先于 age）
    level: Optional[int] = None       # 等级
    signStreak: Optional[int] = None  # 连续签到天数
    description: Optional[str] = None  # 自我介绍（宠物档案）
    health: Optional[Dict[str, Optional[str]]] = None  # 健康信息（类别键->内容，猫/狗身份卡维护；值为 null 时按未填写处理）


class AttachmentInfo(BaseModel):
    """单个多模态附件（图片 / 音频 / 视频）

    由 POST /ai/chat/upload 上传 OSS 后返回，前端原样放进 ChatRequest.attachments；
    url 为 OSS 公网地址（浏览器回放与视觉模型回源拉取共用）；key 为 OSS 对象 key
    （服务端读取转写时校验本人命名空间）；transcript 为音频转写结果
    （由后端转写后回填，前端只读）。
    """

    type: Literal["image", "audio", "video"] = Field(description="附件类型")
    url: str = Field(description="附件地址（OSS 公网 URL）")
    key: Optional[str] = Field(default=None, description="OSS 对象 key")
    mime: str = Field(default="", description="MIME 类型")
    name: str = Field(default="", description="原始文件名")
    size: int = Field(default=0, description="文件大小（字节）")
    transcript: Optional[str] = Field(default=None, description="音频转写文本（后端转写后回填）")


class ChatRequest(BaseModel):
    """POST /ai/chat/stream 请求体"""

    message: str = Field(default="", description="用户本轮输入的文字消息；纯附件时可缺省")
    pet: PetInfo = Field(default_factory=PetInfo, description="当前咨询的宠物信息（缺省时不拼宠物档案）")
    sessionId: Optional[int] = Field(default=None, description="会话 ID；缺省时后端自动新建会话并通过 meta 事件回传")
    attachments: List[AttachmentInfo] = Field(default_factory=list,
                                             description="本轮多模态附件（图片/音频/视频），先经 /ai/chat/upload 上传")


class SessionItem(BaseModel):
    """单个会话条目"""

    id: int                    # 会话 ID
    # 归属宠物 ID（前端据此标注会话属于哪只宠物）；宠物 ID 为雪花 ID（19 位），
    # 超过 JS Number 安全整数上限，必须以字符串下发，否则前端解析时尾数被截断，
    # 按 petId 回溯宠物（会话标签 / 切换确认）会全部失效
    petId: str = "0"
    title: str                 # 会话标题
    createTime: int = 0        # 创建时间（秒级时间戳）
    updateTime: int = 0        # 最近活跃时间（秒级时间戳）


class SessionListData(BaseModel):
    """GET /ai/chat/sessions 返回的 data 结构（会话只按用户隔离，跨宠物统一展示）"""

    sessions: List[SessionItem] = []


class HistoryMessage(BaseModel):
    """单条对话历史消息"""

    role: str        # 角色：user / assistant
    content: str     # 消息文字内容（纯附件的用户消息可能为空串，附件在 attachments 字段）
    petId: Optional[str] = None  # 该轮消息归属的宠物 ID（雪花 ID 用字符串下发防截断；None 表示未知）
    ts: int = 0      # 消息时间戳（秒级）
    interrupted: bool = False    # assistant 回复是否被用户中止生成（历史回放展示「（已停止）」）
    attachments: List[AttachmentInfo] = Field(default_factory=list,
                                              description="该条消息携带的多模态附件（仅用户消息）")


class HistoryData(BaseModel):
    """GET /ai/chat/history 返回的 data 结构"""

    sessionId: int
    messages: List[HistoryMessage] = []


def ok(data: Any = None) -> dict:
    """构造成功 Result：{"code":200,"msg":"success","data":...}"""
    return {"code": 200, "msg": "success", "data": data}


def fail(code: int, msg: str) -> dict:
    """构造失败 Result：{"code":xxx,"msg":"...","data":null}"""
    return {"code": code, "msg": msg, "data": None}


# ============================================================
# 以下为 AI 编排内部 / 结构化输出模型
# ============================================================

# 对话意图枚举：
#   chitchat        闲聊 / 问候，无需检索与工具
#   knowledge       养宠知识咨询（喂养、疫苗、驱虫、行为等），走 RAG
#   health          健康评估相关咨询，走 RAG + 健康评估
#   medical_urgent  疑似疾病 / 用药 / 急症，走 RAG 并强制就医护栏
#   tool_query      需要查询用户真实数据（宠物/订单/购物车/评论/动态）
IntentType = Literal["chitchat", "knowledge", "health", "medical_urgent", "tool_query"]


class IntentResult(BaseModel):
    """意图识别结构化输出"""

    intent: IntentType = Field(description="用户本轮问题的意图分类")
    reason: str = Field(default="", description="简要说明分类依据")


class HealthReminder(BaseModel):
    """单条养护提醒"""

    type: str = Field(description="提醒类型，如 疫苗 / 驱虫 / 体检 / 饮食 / 运动")
    advice: str = Field(description="具体建议")
    urgency: Literal["low", "medium", "high"] = Field(default="low", description="紧急程度")


class PetHealthReport(BaseModel):
    """宠物健康评估报告（LLM 结构化输出）"""

    score: int = Field(ge=0, le=100, description="综合健康评分，0-100")
    level: Literal["excellent", "good", "fair", "warning"] = Field(
        description="评级：excellent 优秀 / good 良好 / fair 一般 / warning 需关注")
    summary: str = Field(description="一句话总体评价")
    risks: List[str] = Field(default_factory=list, description="识别的健康风险点（无则空数组）")
    suggestions: List[str] = Field(default_factory=list, description="养护改进建议")
    care_plan: List[str] = Field(default_factory=list, description="近期养护计划（可执行事项）")
    reminders: List[HealthReminder] = Field(default_factory=list, description="疫苗/驱虫/体检等提醒")
    disclaimer: str = Field(default="本评估基于用户填写信息由 AI 生成，不能替代兽医面诊，如有异常请及时就医。",
                            description="免责声明")


class ReviewSummary(BaseModel):
    """商品评论摘要（LLM 结构化输出）"""

    sentiment: Literal["positive", "neutral", "negative"] = Field(description="整体情感倾向")
    one_line: str = Field(description="一句话总结")
    pros: List[str] = Field(default_factory=list, description="优点列表（来自真实评论）")
    cons: List[str] = Field(default_factory=list, description="缺点列表（来自真实评论）")
    keywords: List[str] = Field(default_factory=list, description="高频关键词")
    count: int = Field(default=0, description="参与总结的评论条数")


class RecommendItem(BaseModel):
    """单条推荐项（LLM 重排输出）"""

    id: int = Field(description="候选内容 ID（商品或动态，取自候选列表）")
    type: Literal["product", "space"] = Field(description="推荐类型：product 商品 / space 动态")
    reason: str = Field(description="推荐理由，结合用户画像，不超过 30 字")
    score: float = Field(default=0.5, description="推荐相关度 0-1")


class RecommendResult(BaseModel):
    """个性化推荐结果（LLM 重排输出）"""

    items: List[RecommendItem] = Field(default_factory=list, description="按相关度降序的推荐列表")
    summary: str = Field(default="", description="整体推荐说明")


# ============================================================
# 长期记忆（LangGraph 官方 runtime store）
# ============================================================

# 单条记忆的归类：preference 偏好 / habit 习性 / fact 事实（如病史）
MemoryKind = Literal["preference", "habit", "fact"]


class MemoryItem(BaseModel):
    """GET /ai/chat/memories 返回的单条长期记忆"""

    key: str                      # store 内的唯一标识（删除接口用）
    scope: Literal["user", "pet"] = Field(description="记忆归属：user 用户级 / pet 宠物级")
    petId: Optional[str] = None   # 宠物级记忆的归属宠物 ID（字符串防精度丢失；user 级为 None）
    petName: Optional[str] = None # 写入时快照的宠物名字（宠物改名/删除后仅作展示参考）
    kind: MemoryKind = "preference"
    content: str                  # 记忆内容（≤50 字）
    createdAt: int = 0            # 创建时间（秒级时间戳）
    updatedAt: int = 0            # 最近更新时间（秒级时间戳）


class MemoryListData(BaseModel):
    """GET /ai/chat/memories 返回的 data 结构"""

    memories: List[MemoryItem] = []


class MemoryOp(BaseModel):
    """单条记忆维护操作（LLM 结构化输出，由 longterm 应用到 store）"""

    action: Literal["add", "update", "delete", "none"] = Field(
        description="add 新增记忆 / update 更新既有记忆（必须带 key）/ delete 删除过时或错误的既有记忆（必须带 key）/ none 本轮无长期记忆变更")
    key: Optional[str] = Field(default=None, description="update / delete 时对应的既有记忆 key；add / none 时留空")
    scope: Literal["user", "pet"] = Field(default="user", description="记忆归属：与具体宠物无关的用户信息用 user；当前宠物的习性/偏好/病史用 pet")
    content: str = Field(default="", description="记忆内容，50 字以内的一条客观事实；action 为 none/delete 时留空")
    kind: MemoryKind = Field(default="preference", description="记忆归类：preference 偏好 / habit 习性 / fact 事实")


class MemoryUpdateResult(BaseModel):
    """长期记忆抽取的结构化输出（本轮问答 → 记忆维护操作列表）"""

    ops: List[MemoryOp] = Field(default_factory=list, description="本轮需要执行的记忆维护操作；无长期有效信息时为空列表或仅一条 none")
