"""ai-service 配置模块

通过 pydantic-settings 读取 ai-service/.env 配置文件，
集中暴露全部配置项，供各模块统一引用（from app.config import settings）。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# ai-service 根目录（app/config.py 的上一级），.env 固定放在这里
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """ai-service 全部配置项（字段名与 .env 中的键一一对应）"""

    # ---------- 服务与 Nacos 注册 ----------
    AI_SERVICE_NAME: str = "ai-service"         # 注册到 Nacos 的服务名（网关 lb://ai-service 依赖它）
    AI_SERVICE_IP: str = "127.0.0.1"            # 注册到 Nacos 的实例 IP
    AI_SERVICE_PORT: int = 8086                 # 服务监听端口
    NACOS_SERVER_ADDR: str = "localhost:8848"   # Nacos 服务端地址

    # ---------- DeepSeek（OpenAI 兼容 API） ----------
    DEEPSEEK_API_KEY: str = ""                  # 为空时自动进入 mock 模式
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # ---------- 对话模式开关 ----------
    MOCK_CHAT: bool = False                     # true 时强制使用 mock 回复（联调 / 无 Key 验证用）

    # ---------- Redis ----------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 3                           # 对话记忆专用 db

    # ---------- 对话记忆 ----------
    HISTORY_MAX_MESSAGES: int = 40              # 每个宠物最多保留的历史消息条数
    HISTORY_TTL_SECONDS: int = 604800           # 历史过期时间（默认 7 天）

    # ---------- LLM 生成参数 ----------
    LLM_MAX_TOKENS: int = 512
    LLM_TEMPERATURE: float = 0.8

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),   # 环境变量文件固定指向 ai-service/.env
        env_file_encoding="utf-8",
        extra="ignore",                    # 忽略 .env 中未声明的键，避免启动报错
        case_sensitive=False,
    )

    @property
    def is_mock(self) -> bool:
        """是否运行在 mock 模式：显式开启 MOCK_CHAT，或未配置 DEEPSEEK_API_KEY 时为 True"""
        return self.MOCK_CHAT or not self.DEEPSEEK_API_KEY.strip()


# 模块级配置单例：全项目统一通过它读取配置
settings = Settings()
