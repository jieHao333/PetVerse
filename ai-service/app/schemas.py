"""数据模型定义

与 Java 端 petverse-common 的 Result / DTO 结构对齐，
保证前后端拿到统一的 {"code":200,"msg":"success","data":...} 报文。
"""
from typing import Any, List, Optional

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


class ChatRequest(BaseModel):
    """POST /ai/chat/stream 请求体"""

    message: str = Field(..., description="用户本轮输入的消息")
    pet: PetInfo = Field(default_factory=PetInfo, description="当前咨询的宠物信息（缺省时不拼宠物档案）")
    sessionId: Optional[int] = Field(default=None, description="会话 ID；缺省时后端自动新建会话并通过 meta 事件回传")


class SessionItem(BaseModel):
    """单个会话条目"""

    id: int                    # 会话 ID
    title: str                 # 会话标题
    createTime: int = 0        # 创建时间（秒级时间戳）
    updateTime: int = 0        # 最近活跃时间（秒级时间戳）


class SessionListData(BaseModel):
    """GET /ai/chat/sessions 返回的 data 结构"""

    petId: int
    sessions: List[SessionItem] = []


class HistoryMessage(BaseModel):
    """单条对话历史消息"""

    role: str        # 角色：user / assistant
    content: str     # 消息内容
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
