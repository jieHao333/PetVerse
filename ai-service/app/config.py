"""全局配置中心：pydantic-settings 装载环境变量与 .env，并把旧版 ``DEEPSEEK_*`` 回落到 ``LLM_*``。

``is_mock`` / ``embedding_enabled`` 等属性读时求值，不要在 import 阶段缓存成模块常量，
否则测试里改 ``settings`` 不会生效。
"""

from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ai-service 包根目录，.env 与语料目录都以它为基准，保证从任意工作目录启动都能找到同一份配置。
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """ai-service 的全部运行参数，字段名即环境变量名（大小写不敏感）。

    未配置的字段走默认值，因此没有 .env 也能启动（进入 mock 模式）。
    """

    # ── 服务自身与注册中心 ────────────────────────────────────────────────
    AI_SERVICE_NAME: str = "ai-service"
    # uvicorn 监听地址：默认只听回环；服务器 / 多实例部署改成 0.0.0.0。
    AI_SERVICE_HOST: str = "127.0.0.1"
    # 注册进 Nacos 的 IP，须是消费方能连上的地址，与监听地址是两回事；留空则自动挑选（见 nacos_client）。
    # 不要写死 127.0.0.1：多机部署会挤在同一 (ip, port) 上互相覆盖，消费方也只会连到自己。
    AI_SERVICE_IP: str = ""
    AI_SERVICE_PORT: int = 8086
    NACOS_SERVER_ADDR: str = "localhost:8848"
    NACOS_GROUP: str = "DEFAULT_GROUP"
    # 空字符串表示 public 命名空间，需与 Java 服务一致。
    NACOS_NAMESPACE: str = ""

    # ── 对话模型（OpenAI 兼容协议） ───────────────────────────────────────
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = ""
    # 兼容旧配置：LLM_* 留空时由 _fallback_to_deepseek 回落到这三个值。
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # ── Embedding（RAG 必备） ─────────────────────────────────────────────
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_MODEL: str = "text-embedding-v3"
    # 必须与 kb_chunk.embedding 的向量维度一致，store 初始化时会校验。
    EMBEDDING_DIM: int = 1024

    # ── 多模态附件 ────────────────────────────────────────────────────────
    CHAT_MAX_ATTACHMENTS: int = 4
    # 图片 / 音频 / 视频分别设限：共用一个阈值会误伤视频或放宽图片。
    MEDIA_MAX_IMAGE_MB: int = 10
    MEDIA_MAX_AUDIO_MB: int = 20
    MEDIA_MAX_VIDEO_MB: int = 50
    # 视觉模型独立配置：多数服务商的对话模型与视觉模型不是同一个。
    LLM_VISION_MODEL: str = ""
    LLM_VISION_API_KEY: str = ""
    LLM_VISION_BASE_URL: str = ""
    # ASR 走 OpenAI 兼容的 /audio/transcriptions 协议。
    ASR_API_KEY: str = ""
    ASR_BASE_URL: str = ""
    ASR_MODEL: str = ""

    # ── 附件存储（阿里云 OSS） ────────────────────────────────────────────
    OSS_ENDPOINT: str = ""
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_BUCKET_NAME: str = ""
    # 可选自定义域名；留空按 {bucket}.{endpoint} 拼默认公网地址。
    OSS_DOMAIN: str = ""

    # 置 true 时不调用真实模型，便于无 Key 环境演示与联调。
    MOCK_CHAT: bool = False

    # ── Redis（热缓存 + 并发计数，AI 模块统一用 db=1） ────────────────────
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 1

    # ── PostgreSQL + pgvector（会话 / 记忆 / 向量 / 缓存温层） ─────────────
    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    PG_USER: str = "postgres"
    PG_PASSWORD: str = "123456"
    PG_DB: str = "petverse_ai"
    PG_POOL_MIN: int = 1
    PG_POOL_MAX: int = 10
    # 启动时自动创建 LangGraph 官方 checkpoint/store 表（幂等）；关闭后仍会在首次使用时惰性建表。
    PG_AUTO_CREATE_TABLES: bool = True

    # ── RAG 检索参数（调参说明见 README「配置说明」） ─────────────────────
    RAG_ENABLED: bool = True
    RAG_TOP_K: int = 5
    RAG_RECALL_K: int = 20
    # 双门槛之一：向量余弦相似度下限，低于它且关键词命中不足则丢弃。
    RAG_MIN_SIM: float = 0.30
    # 双门槛之二：关键词命中词数下限（随查询词数按比例抬升，见 lexicon.LexIndex.search）。
    RAG_LEX_MIN_TERMS: int = 2
    # 融合权重：越大越偏向量通道，0.85 表示以向量为主、关键词兜底修正。
    RAG_FUSION_ALPHA: float = 0.85
    # 物种不符：true 直接过滤，false 按系数降权保留（默认降权，避免误杀通用文档）。
    RAG_SPECIES_STRICT: bool = False
    RAG_SPECIES_PENALTY: float = 0.5
    # 多轮追问改写：短句或含指代词的追问先改写成自包含问句再检索。
    RAG_QUERY_REWRITE: bool = True
    RAG_REWRITE_MIN_CHARS: int = 12

    # ── 知识库导入与切分 ──────────────────────────────────────────────────
    # 留空回落到 app/knowledge（见 knowledge_dir）。
    KNOWLEDGE_DIR: str = ""
    # 切分三档：目标长度 / 合并下限（过短小节并入上一节）/ 硬上限（超长句强制截断）。
    KB_CHUNK_SIZE: int = 600
    KB_CHUNK_MIN: int = 80
    KB_CHUNK_MAX: int = 900
    EMBED_BATCH_SIZE: int = 10
    KB_MAX_UPLOAD_MB: int = 20
    # 上传原文留存目录，留空回落到 data/kb（供预览重解析与原文下载）。
    KB_STORAGE_DIR: str = ""
    # 绕过网关跑运维时的备选凭证，与 ADMIN 角色二选一。
    KB_ADMIN_TOKEN: str = ""
    # Skill 技能包根目录（每个技能一个 <name>/SKILL.md 包），留空回落到包内 skills/。
    SKILLS_DIR: str = ""

    # ── 下游业务服务发现（ai-service 直连实例，注入内部 X-User-Id） ───────
    # 服务间令牌：随下游调用以 X-Internal-Token 发送，业务侧校验通过才信任 X-User-Id。
    # 必须与单体版 auth.internal-token 一致；留空则退回「按来源 IP 信任」，跨主机部署必然失效。
    INTERNAL_TOKEN: str = ""
    # microservice：按下列服务名分别发现；monolith：统一路由到 MONOLITH_SERVICE_NAME。
    SERVICE_DEPLOY_MODE: Literal["microservice", "monolith"] = "microservice"
    MONOLITH_SERVICE_NAME: str = "petverse-single"
    PET_SERVICE_NAME: str = "pet-service"
    SPACE_SERVICE_NAME: str = "space-service"
    USER_SERVICE_NAME: str = "user-service"
    SOCIAL_SERVICE_NAME: str = "social-service"
    REMARK_SERVICE_NAME: str = "remark-service"
    SHOP_SERVICE_NAME: str = "shop-service"
    DATA_SERVICE_NAME: str = "data-service"
    # 实例列表缓存秒数；刷新失败时沿用旧列表，避免 Nacos 抖动放大成业务故障。
    SERVICE_DISCOVERY_CACHE_TTL: float = 10.0
    HTTP_TIMEOUT: float = 8.0

    # ── 结果缓存 TTL（秒） ────────────────────────────────────────────────
    REVIEW_SUMMARY_TTL: int = 21600
    RECOMMEND_CACHE_TTL: int = 300

    # 送入 LLM 的历史消息条数（从 checkpoint 记忆里裁剪最近 N 条）。
    HISTORY_MAX_MESSAGES: int = 40

    # ── 长期记忆 ──────────────────────────────────────────────────────────
    MEMORY_ENABLED: bool = True
    # 单轮注入 Prompt 的记忆条数上限，也是召回上限。
    MEMORY_MAX_ITEMS: int = 20

    # ── 自主 Agent（AgentLoop） ────────────────────────────────────────────
    # 单轮对话内「模型 → 工具」的最大循环次数，超限后强制无工具作答，
    # 防止反复查同一件事把成本与延迟拉爆；3 轮够覆盖「先查档案再查订单」这类组合问题。
    AGENT_MAX_STEPS: int = 3
    # 启用的工具分组（逗号分隔），未知分组忽略。关掉某组等价于让模型「看不见」这些工具，
    # 比在提示词里劝它别调用可靠。
    # 只读：knowledge / skill / pet / shop / community / profile
    # 写操作：cart 加购 · interaction 点赞 · pet_edit 档案维护（可撤销）
    #         publish 发布动态（默认开启，每次走人工审批）
    #         content 评论（默认关闭，公开发布且暂无审批）
    # 下单与支付不是可配置分组：交易闭环不开放给模型，AI 的电商能力止于加购。
    AGENT_TOOL_GROUPS: str = "knowledge,skill,pet,shop,cart,community,interaction,pet_edit,publish,profile"
    # 需人工审批（Human-in-the-loop）的工具，逗号分隔。命中时图在工具执行前挂起，
    # 前端弹卡片、用户确认后从 checkpoint 继续；无 checkpointer 时直接拒绝执行。
    AGENT_APPROVAL_TOOLS: str = "publish_post"

    # ── AI 扩展（管理员在后台配置的 MCP 服务与 Skill 技能） ────────────────
    # MCP 总开关：false 时即使库里有启用中的服务也不装配远程工具（Skill 不受影响）。
    AGENT_MCP_ENABLED: bool = True
    # 单个 MCP 服务的连接 / 列工具超时（秒）：不可达的服务只拖累装配，不拖死对话。
    MCP_LOAD_TIMEOUT: float = 10.0
    # 扩展配置的进程内缓存 TTL（秒）：管理接口写入会显式失效，TTL 兜底多 worker 部署。
    MCP_CACHE_TTL: int = 60

    # ── 生成参数 ──────────────────────────────────────────────────────────
    LLM_MAX_TOKENS: int = 512
    LLM_TEMPERATURE: float = 0.8

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        # 忽略 .env 里的历史残留变量，避免升级后启动失败。
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _fallback_to_deepseek(self) -> "Settings":
        """把旧版 ``DEEPSEEK_*`` 回落到 ``LLM_*``，让只配了旧变量的部署升级后仍可用。"""
        if not self.LLM_API_KEY:
            self.LLM_API_KEY = self.DEEPSEEK_API_KEY
        if not self.LLM_BASE_URL:
            self.LLM_BASE_URL = self.DEEPSEEK_BASE_URL
        if not self.LLM_MODEL:
            self.LLM_MODEL = self.DEEPSEEK_MODEL
        # 视觉通道未单独配置时复用主模型凭证——同源服务商通常是同一把 Key。
        if not self.LLM_VISION_API_KEY:
            self.LLM_VISION_API_KEY = self.LLM_API_KEY
        if not self.LLM_VISION_BASE_URL:
            self.LLM_VISION_BASE_URL = self.LLM_BASE_URL
        return self

    @property
    def is_mock(self) -> bool:
        """是否模拟模式：缺 Key 也算 mock，让无凭证环境仍能跑通全链路（启动日志会告警）。"""
        return self.MOCK_CHAT or not self.LLM_API_KEY.strip()

    @property
    def llm_enabled(self) -> bool:
        """是否接入真实对话模型（``is_mock`` 的反向语义）。"""
        return not self.is_mock

    @property
    def embedding_enabled(self) -> bool:
        """Embedding 是否可用；Key 与模型名缺一不可（只配 Key 请求必然失败）。"""
        return bool(self.EMBEDDING_API_KEY.strip() and self.EMBEDDING_MODEL.strip())

    @property
    def vision_enabled(self) -> bool:
        """多模态看图是否可用（mock 模式下恒为 false）。"""
        return (not self.is_mock) and bool(self.LLM_VISION_MODEL.strip()
                                           and self.LLM_VISION_API_KEY.strip()
                                           and self.LLM_VISION_BASE_URL.strip())

    @property
    def asr_enabled(self) -> bool:
        """语音转写是否可用；不可用时音频只做占位说明，不影响文字对话。"""
        return bool(self.ASR_API_KEY.strip() and self.ASR_BASE_URL.strip() and self.ASR_MODEL.strip())

    @property
    def oss_configured(self) -> bool:
        """附件存储是否配置完整（四项缺一不可）。"""
        return bool(self.OSS_ENDPOINT.strip() and self.OSS_ACCESS_KEY_ID.strip()
                    and self.OSS_ACCESS_KEY_SECRET.strip() and self.OSS_BUCKET_NAME.strip())

    @property
    def pg_dsn(self) -> str:
        """psycopg 直连串（同步驱动 / 连接池使用）。"""
        return (f"postgresql://{self.PG_USER}:{self.PG_PASSWORD}"
                f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}")

    @property
    def pg_conn_str(self) -> str:
        """SQLAlchemy 风格连接串（仅评测脚本里的旧 PGVector 集合使用）。"""
        return (f"postgresql+psycopg://{self.PG_USER}:{self.PG_PASSWORD}"
                f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}")

    @property
    def knowledge_dir(self) -> Path:
        """仓库语料目录；未配置时取包内 ``app/knowledge``。"""
        raw = (self.KNOWLEDGE_DIR or "").strip()
        return Path(raw) if raw else BASE_DIR / "app" / "knowledge"

    @property
    def kb_storage_dir(self) -> Path:
        """上传原文留存目录；未配置时取 ``data/kb``（与仓库语料分开，便于区分来源）。"""
        raw = (self.KB_STORAGE_DIR or "").strip()
        return Path(raw) if raw else BASE_DIR / "data" / "kb"

    @property
    def skills_dir(self) -> Path:
        """Skill 技能包根目录；未配置时取包内 ``skills/``。"""
        raw = (self.SKILLS_DIR or "").strip()
        return Path(raw) if raw else BASE_DIR / "skills"


# 进程级单例，所有模块统一 ``from app.config import settings``。
settings = Settings()
