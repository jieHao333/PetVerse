"""管理端接口的身份校验（知识库 / AI 扩展管理共用）。

把「管理员身份」的判定收敛到一处——网关鉴权后注入的 ``X-User-Role`` 为主通道，
``X-Kb-Admin`` + ``KB_ADMIN_TOKEN`` 为「不经网关直连本服务跑运维」的备选通道。

鉴权说明：网关会先剥离客户端自带的 X-User-Id / X-User-Role 再按 JWT 重新注入，
因此经网关的请求可以信任这两个头；直连 8086 端口的进程能伪造角色头，
备选令牌通道就是为这种运维场景保留的——令牌必须非空才启用，
否则「配置为空 + 请求头为空」会直接放行所有人。
"""

from typing import Optional

from fastapi.responses import JSONResponse

from app.config import settings
from app.schemas import fail

ROLE_ADMIN = "ADMIN"


def parse_uid(raw: Optional[str]) -> Optional[int]:
    """解析请求头里的用户 ID。

    Returns:
        正整数用户 ID；缺失或非法时返回 None。
    """
    if not raw:
        return None
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def admin_denied(x_user_id: Optional[str], x_user_role: Optional[str],
                 x_kb_admin: Optional[str], resource: str) -> Optional[JSONResponse]:
    """管理员校验。

    两条通道任一通过即可：备选令牌（直连运维）或网关鉴权后的 ADMIN 角色。

    Args:
        x_user_id: 用户 ID 请求头（用于区分「未登录」与「无权限」）。
        resource: 资源名称（拼进拒绝文案，如「知识库」「AI 扩展」）。

    Returns:
        None 表示校验通过；否则返回可直接下发的拒绝报文（401 未登录 / 403 无权限）。
    """
    token = (settings.KB_ADMIN_TOKEN or "").strip()
    # 令牌必须非空才启用该通道：否则「配置为空 + 请求头为空」会直接放行所有人。
    if token and (x_kb_admin or "").strip() == token:
        return None
    if parse_uid(x_user_id) is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    if (x_user_role or "").strip().upper() == ROLE_ADMIN:
        return None
    return JSONResponse(status_code=200, content=fail(403, f"仅管理员可管理{resource}"))