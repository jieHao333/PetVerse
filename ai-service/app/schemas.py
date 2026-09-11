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
    age: Optional[int] = None         # 年龄
    level: Optional[int] = None       # 等级
    signStreak: Optional[int] = None  # 连续签到天数
    description: Optional[str] = None  # 自我介绍（宠物档案）
    health: Optional[Dict[str, Optional[str]]] = None  # 健康信息（类别键->内容，猫/狗身份卡维护；值为 null 时按未填写处理）


class ChatRequest(BaseModel):
    """POST /ai/chat/stream 请求体"""

    message: str = Field(..., description="用户本轮输入的消息")
    pet: PetInfo = Field(default_factory=PetInfo, description="当前咨询的宠物信息（缺省时不拼宠物档案）")
    sessionId: Optional[int] = Field(default=None, description="会话 ID；缺省时后端自动新建会话并通过 meta 事件回传")


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
    content: str     # 消息内容
    petId: Optional[str] = None  # 该轮消息归属的宠物 ID（雪花 ID 用字符串下发防截断；None 表示未知）
    ts: int = 0      # 消息时间戳（秒级）


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
