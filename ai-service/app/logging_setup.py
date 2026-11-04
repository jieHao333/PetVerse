"""日志初始化工具。

职责：为服务进程与命令行脚本提供统一的日志格式，保证 uvicorn 启动日志与业务日志
在时间戳、级别、logger 名称三列上对齐，便于按 ``[app.模块名]`` 过滤排查。

主要导出：``setup_logging``。

外部依赖：标准库 ``logging``（无第三方依赖）。

使用示例：
    from app.logging_setup import setup_logging

    setup_logging()                 # 默认 INFO，与 main.py / scripts 保持一致
    setup_logging(logging.DEBUG)    # 排查问题时临时放宽
"""

import logging


def setup_logging(level: int = logging.INFO) -> None:
    """配置根 logger 的输出级别与格式。

    仅在进程入口调用一次即可：``logging.basicConfig`` 对已配置过的根 logger 不再生效，
    因此重复调用是幂等的（不会叠加 handler）。

    Args:
        level: 根 logger 级别，默认 ``logging.INFO``；传入 ``DEBUG`` 可看到检索/工具链路的细节。

    Returns:
        None。配置直接作用于全局 logging 状态。

    Raises:
        无。
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
