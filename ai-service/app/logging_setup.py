"""日志初始化：为服务进程与命令行脚本提供统一格式（时间 / 级别 / logger 名）。"""

import logging

# httpx 对每个出站请求都打一条 INFO，而 Nacos 心跳每 5 秒一次、对话还会持续调用 LLM，
# 不压制会刷屏；只收紧这一档噪音，WARNING 及以上仍照常输出。
_THIRD_PARTY_LOGGERS = ("httpx", "httpcore")


def setup_logging(level: int = logging.INFO) -> None:
    """配置根 logger，进程入口调用一次即可（basicConfig 重复调用不会叠加 handler）。"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # DEBUG 级别下不再降噪，便于看到每条出站请求。
    third_party_level = level if level <= logging.DEBUG else logging.WARNING
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(third_party_level)
