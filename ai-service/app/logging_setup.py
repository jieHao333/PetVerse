"""统一日志初始化

供 main.py 与独立脚本（如知识库导入）复用，避免各处重复配置格式。
"""
import logging


def setup_logging(level: int = logging.INFO) -> None:
    """配置根日志格式（重复调用无副作用，basicConfig 只在首次生效）"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
