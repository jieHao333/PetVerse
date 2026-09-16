"""ai-service 配置模块

通过 pydantic-settings 读取 ai-service/.env 配置文件，
集中暴露全部配置项，供各模块统一引用（from app.config import settings）。

配置分三块：
  1. 对话模型（LLM）：统一 OpenAI 格式，任意兼容服务（DeepSeek / 阿里云百炼等）
     只需改 LLM_BASE_URL + LLM_MODEL + LLM_API_KEY，无需改代码；保留 DEEPSEEK_*
     作为向后兼容别名（LLM_* 未显式配置时自动回落）。
  2. Embedding：OpenAI 格式，供 RAG 检索使用（DeepSeek 无 embedding 接口，必须另配）。
  3. pgvector：RAG 向量存储，复用本机 PostgreSQL + pgvector 插件。
"""
from pathlib import Path

from pydantic import model_validator
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

    # ---------- 对话模型（OpenAI 兼容格式，可配 base_url） ----------
    LLM_API_KEY: str = ""                       # 为空时自动进入 mock 模式
    LLM_BASE_URL: str = ""                      # 例如 https://api.deepseek.com/v1 或 https://dashscope.aliyuncs.com/compatible-mode/v1
    LLM_MODEL: str = ""                         # 例如 deepseek-chat / qwen-plus
    # 兼容旧配置：LLM_* 未显式配置时回落到以下 DeepSeek 默认值
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # ---------- Embedding（OpenAI 兼容格式，RAG 检索用） ----------
    EMBEDDING_API_KEY: str = ""                 # 为空时 RAG 自动降级为关键词检索
    EMBEDDING_BASE_URL: str = ""                # 例如阿里云 https://dashscope.aliyuncs.com/compatible-mode/v1
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIM: int = 1024                   # 必须与 pgvector 建表维度一致（v3 支持 1024/768/512）

    # ---------- 对话模式开关 ----------
    MOCK_CHAT: bool = False                     # true 时强制使用 mock 回复（联调 / 无 Key 验证用）

    # ---------- Redis ----------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 3                           # 对话记忆专用 db

    # ---------- PostgreSQL（业务持久化：会话/消息 + checkpoint + RAG + 健康报告） ----------
    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    PG_USER: str = "postgres"
    PG_PASSWORD: str = "123456"
    PG_DB: str = "petverse_ai"
    PG_POOL_MIN: int = 1
    PG_POOL_MAX: int = 10

    # ---------- RAG 检索参数 ----------
    RAG_ENABLED: bool = True                    # 关闭后对话跳过知识检索，直接生成
    RAG_TOP_K: int = 4                          # 每次检索召回的知识片段数
    RAG_SCORE_THRESHOLD: float = 0.35           # 相似度阈值（低于该值的片段丢弃，避免答非所问）
    RAG_COLLECTION: str = "petverse_kb"         # pgvector 集合名

    # ---------- 业务服务地址（ai-service 直连各微服务拉取上下文） ----------
    PET_SERVICE_URL: str = "http://127.0.0.1:8081"      # 宠物档案 / 健康信息
    SPACE_SERVICE_URL: str = "http://127.0.0.1:8082"    # 动态（推荐候选）
    USER_SERVICE_URL: str = "http://127.0.0.1:8083"     # 用户资料
    SOCIAL_SERVICE_URL: str = "http://127.0.0.1:8084"   # 好友
    REMARK_SERVICE_URL: str = "http://127.0.0.1:8085"   # 点赞 / 评论
    SHOP_SERVICE_URL: str = "http://127.0.0.1:8087"     # 商品 / 订单 / 评论
    HTTP_TIMEOUT: float = 8.0                   # 业务服务调用超时（秒），失败降级为空

    # ---------- 功能缓存 TTL（秒） ----------
    REVIEW_SUMMARY_TTL: int = 21600             # 评论摘要缓存 6 小时（有新增评论自然过期重建）
    RECOMMEND_CACHE_TTL: int = 600              # 个性化推荐缓存 10 分钟
    HEALTH_CACHE_TTL: int = 3600                # 健康评估结果缓存 1 小时

    # ---------- 对话记忆 ----------
    # LLM 上下文窗口条数：compose 组装时从 checkpoint 记忆裁剪最近 N 条，控制 token 成本；
    # 同时作为存量会话（升级前只有 MySQL 历史）首次接入 checkpoint 时的回填上限
    HISTORY_MAX_MESSAGES: int = 40

    # ---------- LLM 生成参数 ----------
    LLM_MAX_TOKENS: int = 512
    LLM_TEMPERATURE: float = 0.8

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),   # 环境变量文件固定指向 ai-service/.env
        env_file_encoding="utf-8",
        extra="ignore",                    # 忽略 .env 中未声明的键，避免启动报错
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _fallback_to_deepseek(self) -> "Settings":
        """LLM_* 未显式配置时，回落到 DEEPSEEK_* 旧配置，保证平滑迁移

        注意：Embedding 不自动复用 LLM 的 Key/Base——DeepSeek 无 embedding 接口，
        且不同服务商密钥不通用；Embedding 必须单独配置，未配置时 RAG 自动降级为
        关键词检索（不会尝试连接 pgvector）。
        """
        if not self.LLM_API_KEY:
            self.LLM_API_KEY = self.DEEPSEEK_API_KEY
        if not self.LLM_BASE_URL:
            self.LLM_BASE_URL = self.DEEPSEEK_BASE_URL
        if not self.LLM_MODEL:
            self.LLM_MODEL = self.DEEPSEEK_MODEL
        return self

    @property
    def is_mock(self) -> bool:
        """是否运行在 mock 模式：显式开启 MOCK_CHAT，或未配置 LLM_API_KEY 时为 True"""
        return self.MOCK_CHAT or not self.LLM_API_KEY.strip()

    @property
    def llm_enabled(self) -> bool:
        """真实 LLM 是否可用"""
        return not self.is_mock

    @property
    def embedding_enabled(self) -> bool:
        """Embedding 是否可用（RAG 向量检索的前提；不可用时降级为关键词检索）"""
        return bool(self.EMBEDDING_API_KEY.strip() and self.EMBEDDING_MODEL.strip())

    @property
    def rag_enabled(self) -> bool:
        """RAG 是否可用：显式开关 + Embedding 就绪"""
        return self.RAG_ENABLED and self.embedding_enabled

    @property
    def pg_dsn(self) -> str:
        """psycopg 原生连接串（无 SQLAlchemy 驱动前缀）"""
        return (f"postgresql://{self.PG_USER}:{self.PG_PASSWORD}"
                f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}")

    @property
    def pg_conn_str(self) -> str:
        """SQLAlchemy / LangChain-Postgres 连接串（显式指定 psycopg 驱动）"""
        return (f"postgresql+psycopg://{self.PG_USER}:{self.PG_PASSWORD}"
                f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}")


# 模块级配置单例：全项目统一通过它读取配置
settings = Settings()
