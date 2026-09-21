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
    EMBEDDING_API_KEY: str = ""                 # 为空时知识库检索不可用（知识类提问直接报错）
    EMBEDDING_BASE_URL: str = ""                # 例如阿里云 https://dashscope.aliyuncs.com/compatible-mode/v1
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIM: int = 1024                   # 必须与 pgvector 建表维度一致（v3 支持 1024/768/512）

    # ---------- 多模态附件（图片 / 音频 / 视频，直传 OSS，见下方 OSS_* 配置） ----------
    CHAT_MAX_ATTACHMENTS: int = 4               # 单轮对话附件数上限
    MEDIA_MAX_IMAGE_MB: int = 10                # 单张图片大小上限（MB）
    MEDIA_MAX_AUDIO_MB: int = 20                # 单条音频大小上限（MB）
    MEDIA_MAX_VIDEO_MB: int = 50                # 单个视频大小上限（MB）
    # 视觉理解模型（OpenAI 兼容多模态对话，如阿里云百炼 qwen-vl-plus / qwen-vl-max）。
    # Key / BaseURL 缺省时自动回落到 LLM_*；模型也未配置时图片降级为文字占位提示
    LLM_VISION_MODEL: str = ""
    LLM_VISION_API_KEY: str = ""
    LLM_VISION_BASE_URL: str = ""
    # 语音转写（OpenAI 兼容 /audio/transcriptions 接口，如百炼 paraformer-v2 / whisper）。
    # 未配置时音频降级为文字占位提示（不影响对话主流程）
    ASR_API_KEY: str = ""
    ASR_BASE_URL: str = ""                      # 例如 https://dashscope.aliyuncs.com/compatible-mode/v1
    ASR_MODEL: str = ""                         # 例如 paraformer-v2

    # ---------- 阿里云 OSS（多模态附件持久化，与 Java 侧 petverse-common 共用同一个桶） ----------
    # 四项配置齐全后附件直传 OSS（对象 key：ai-chat/{userId}/{yyyyMMdd}/{uuid}.ext），
    # 公网 URL 同时用于浏览器回放与视觉模型回源拉取；缺任一项时上传接口明确报错
    OSS_ENDPOINT: str = ""                      # 例如 oss-cn-beijing.aliyuncs.com
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_BUCKET_NAME: str = ""
    OSS_DOMAIN: str = ""                        # 可选：自定义 / CDN 访问域名（含协议）

    # ---------- 对话模式开关 ----------
    MOCK_CHAT: bool = False                     # true 时强制使用 mock 回复（联调 / 无 Key 验证用）

    # ---------- Redis ----------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 3                           # AI 结果缓存专用 db（评论摘要 / 推荐；经 app/cache.py 两级门面读写）

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

    # ---------- 功能缓存 TTL（秒；Redis 热层与 ai_cache 温层共用） ----------
    REVIEW_SUMMARY_TTL: int = 21600             # 评论摘要缓存 6 小时（有新增评论自然过期重建）
    RECOMMEND_CACHE_TTL: int = 600              # 个性化推荐缓存 10 分钟

    # ---------- 对话记忆 ----------
    # LLM 上下文窗口条数：compose 组装时从 checkpoint 记忆裁剪最近 N 条，控制 token 成本；
    # 同时作为存量会话（升级前只有 MySQL 历史）首次接入 checkpoint 时的回填上限
    HISTORY_MAX_MESSAGES: int = 40

    # ---------- 长期记忆（LangGraph 官方 runtime store，跨会话） ----------
    MEMORY_ENABLED: bool = True                 # 总开关：关闭后既不注入也不抽取（store 表保留）
    MEMORY_MAX_ITEMS: int = 20                  # 注入 System Prompt 的记忆条数上限（用户级 + 宠物级合计）

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
        且不同服务商密钥不通用；Embedding 必须单独配置，未配置时知识库检索
        不可用（知识类提问会明确报错，不会静默跳过）。
        视觉模型同样单独回落：LLM_VISION_API_KEY / LLM_VISION_BASE_URL 缺省时
        复用 LLM_*（同一服务商同时提供文本与视觉模型时只需配一个 MODEL）。
        """
        if not self.LLM_API_KEY:
            self.LLM_API_KEY = self.DEEPSEEK_API_KEY
        if not self.LLM_BASE_URL:
            self.LLM_BASE_URL = self.DEEPSEEK_BASE_URL
        if not self.LLM_MODEL:
            self.LLM_MODEL = self.DEEPSEEK_MODEL
        if not self.LLM_VISION_API_KEY:
            self.LLM_VISION_API_KEY = self.LLM_API_KEY
        if not self.LLM_VISION_BASE_URL:
            self.LLM_VISION_BASE_URL = self.LLM_BASE_URL
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
        """Embedding 是否可用（RAG 向量检索的前提；不可用时知识检索直接报错）"""
        return bool(self.EMBEDDING_API_KEY.strip() and self.EMBEDDING_MODEL.strip())

    @property
    def vision_enabled(self) -> bool:
        """视觉理解是否可用：真实模式 + 配置了视觉模型（Key/Base 已回落到 LLM_*）"""
        return (not self.is_mock) and bool(self.LLM_VISION_MODEL.strip()
                                           and self.LLM_VISION_API_KEY.strip()
                                           and self.LLM_VISION_BASE_URL.strip())

    @property
    def asr_enabled(self) -> bool:
        """语音转写是否可用：三项配置齐全（未配置时音频降级为文字占位提示）"""
        return bool(self.ASR_API_KEY.strip() and self.ASR_BASE_URL.strip() and self.ASR_MODEL.strip())

    @property
    def oss_configured(self) -> bool:
        """OSS 配置是否齐全：四项必填（缺任一项时附件回落本服务磁盘存储）"""
        return bool(self.OSS_ENDPOINT.strip() and self.OSS_ACCESS_KEY_ID.strip()
                    and self.OSS_ACCESS_KEY_SECRET.strip() and self.OSS_BUCKET_NAME.strip())

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
