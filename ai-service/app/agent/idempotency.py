"""对话内写操作的幂等保护。

同一轮对话里，同一个写操作（相同工具 + 相同参数）最多真正执行一次。
命中时直接返回上一次的成功结果，不再发起下游请求。

为什么需要它：
    1. 部分模型会在一条回复里把同一个调用发两遍（尤其并行 tool_calls 场景）；
    2. 路由层的「首帧前失败自动重试」会重新跑一遍图，已经执行过的写操作会被再执行一次；
    3. 加购接口是**累加**语义（``POST /shop/cart`` 重复调用会把数量翻倍）——
       重复执行不是「多此一举」，而是真实的数据错误。

为什么只缓存成功结果：
    失败可能源于瞬时故障（超时、限流），用户重试应当真正重试；成功则不可重复。

作用域与局限：
    - key 里带 ``run_id``（每轮对话生成一次），所以跨轮次不会误判「用户就是想再加一件」；
    - 缓存是**进程内**的。多 worker 部署时，重试若落到另一个进程会重新执行一次；
      因此这是「降低概率」而不是「分布式事务级保证」。真要强保证需要下游支持幂等键。
"""

import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 缓存条目存活时间：足够覆盖一次请求 + 重试；再长只会白占内存。
_TTL_SECONDS = 900.0
# 容量上限：超过就整体清理过期项，仍超限则清空（宁可少去重，也不要无限增长）。
_MAX_ENTRIES = 4096

# key -> (写入时间, 结果文本)
_CACHE: Dict[str, Tuple[float, str]] = {}


def _fingerprint(tool: str, args: Dict[str, Any]) -> str:
    """把「工具名 + 参数」压成一个稳定指纹。

    Args:
        args: 工具参数（应只包含模型可见参数，不含注入的状态）。

    Returns:
        十六进制摘要；参数顺序不同但语义相同时指纹一致。
    """
    try:
        raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(args)
    return hashlib.sha1(f"{tool}|{raw}".encode("utf-8")).hexdigest()


def _key(run_id: str, tool: str, args: Dict[str, Any]) -> str:
    """拼接缓存 key。

    Returns:
        缓存 key；``run_id`` 为空时返回空串（表示不做去重）。
    """
    if not run_id:
        return ""
    return f"{run_id}:{_fingerprint(tool, args)}"


def _sweep(now: float) -> None:
    """清理过期条目，必要时整体清空。
    """
    expired = [k for k, (ts, _) in _CACHE.items() if now - ts > _TTL_SECONDS]
    for key in expired:
        _CACHE.pop(key, None)
    if len(_CACHE) > _MAX_ENTRIES:
        logger.warning("幂等缓存超过上限，整体清空: size=%s", len(_CACHE))
        _CACHE.clear()


def get(run_id: str, tool: str, args: Dict[str, Any]) -> Optional[str]:
    """查询本轮是否已成功执行过同一操作。

    Args:
        run_id: 本轮对话标识（来自图状态）。

    Returns:
        上次的成功结果文本；未执行过或已过期时返回 None。
    """
    key = _key(run_id, tool, args)
    if not key:
        return None
    item = _CACHE.get(key)
    if item is None:
        return None
    ts, result = item
    if time.time() - ts > _TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return result


def put(run_id: str, tool: str, args: Dict[str, Any], result: str) -> None:
    """记录一次成功执行。

    Args:
        result: 返回给模型的结果文本（命中时原样重放，保证模型看到一致的信息）。
    """
    key = _key(run_id, tool, args)
    if not key:
        return
    now = time.time()
    if len(_CACHE) >= _MAX_ENTRIES:
        _sweep(now)
    _CACHE[key] = (now, result)


def clear() -> None:
    """清空缓存（仅供测试与排障使用）。
    """
    _CACHE.clear()
