"""数据模型定义

与 Java 端 petverse-common 的 Result / DTO 结构对齐，
保证前后端拿到统一的 {"code":200,"msg":"success","data":...} 报文。
"""
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class PetInfo(BaseModel):
    """宠物信息（对话人设输入）

    字段全部可选容错：前端少传 / 漏传任何字段都不影响对话主流程，
    人设构建时会用默认值兜底。
    """

    id: Optional[int] = None          # 宠物 ID（Redis 历史 key 的一部分）
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
    pet: PetInfo = Field(default_factory=PetInfo, description="当前对话的宠物信息（缺省时用兜底人设）")


class HistoryMessage(BaseModel):
    """单条对话历史消息"""

    role: str        # 角色：user / assistant
    content: str     # 消息内容
    ts: int = 0      # 消息时间戳（秒级）


class HistoryData(BaseModel):
    """GET /ai/chat/history 返回的 data 结构"""

    petId: int
    messages: List[HistoryMessage] = []


def ok(data: Any = None) -> dict:
    """构造成功 Result：{"code":200,"msg":"success","data":...}"""
    return {"code": 200, "msg": "success", "data": data}


def fail(code: int, msg: str) -> dict:
    """构造失败 Result：{"code":xxx,"msg":"...","data":null}"""
    return {"code": code, "msg": msg, "data": None}
