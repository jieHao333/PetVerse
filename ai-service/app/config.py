"""全局配置中心。

职责：用 pydantic-settings 从环境变量与 ``ai-service/.env`` 装载全部运行参数，并在装载后
做一次「缺省回落」，把历史遗留的 ``DEEPSEEK_*`` 配置补齐到新的 ``LLM_*`` 上；同时把散落在
各模块里的「某能力是否可用」判断收敛成一组 ``is_xxx`` 属性，避免每个调用点各写一遍。

主要导出：
    - ``Settings``  配置模型（字段即环境变量名，大小写不敏感）
    - ``settings``  进程级单例，import 时即完成装载与回落

外部依赖：``pydantic-settings``（读取 .env）、``pathlib``。

使用示例：
    from app.config import settings
    from app.service_discovery import discovery

    if settings.is_mock:                      # 没有真实 Key 时统一走内置 mock
        ...
    dsn = settings.pg_dsn                     # psycopg 直连串
    base_url = await discovery.resolve(settings.PET_SERVICE_NAME)  # 轮询一个健康实例

注意：``is_mock`` / ``embedding_enabled`` 等属性是「读时求值」的，不要在 import 阶段就把
结果缓存成模块常量——测试里改 ``settings`` 后应当立即生效。
"""

from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ai-service 包根目录（app 的上一级），.env 与语料目录都以它为基准，
# 保证从任意工作目录启动进程都能找到同一份配置。
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """ai-service 的全部运行参数。

    字段名与环境变量名一一对应（``case_sensitive=False``），未在 .env / 环境中出现的字段
    使用下面的默认值，因此本地不配任何变量也能启动（会进入 mock 模式）。
    """

    # ── 服务自身与注册中心 ────────────────────────────────────────────────
    AI_SERVICE_NAME: str = "ai-service"
    # 监听地址（uvicorn 的 bind）：默认只听回环，本机开发最安全。
    # 服务器 / 多实例部署改成 0.0.0.0，让注册出去的地址真的能被别的机器连上。
    AI_SERVICE_HOST: str = "127.0.0.1"
    # 注册进 Nacos 的 IP 必须是「消费方（网关 / 单体）能连上」的地址，与监听地址是两回事：
    # 监听 0.0.0.0 时本机有多个网卡，必须挑一个写出去。留空 = 自动挑（见 nacos_client）。
    # 不要写死 127.0.0.1：多机部署时所有实例会挤在同一个 (ip, port) 上互相覆盖，
    # 且消费方拿到 127.0.0.1 只会连到自己。
    AI_SERVICE_IP: str = ""
    AI_SERVICE_PORT: int = 8086
    NACOS_SERVER_ADDR: str = "localhost:8848"
    NACOS_GROUP: str = "DEFAULT_GROUP"
    # 空字符串表示 public 命名空间；需与 Java 服务注册时使用的命名空间一致。
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
    # 必须与 kb_chunk.embedding 的向量维度一致，store 初始化时会做校验并明确报错。
    EMBEDDING_DIM: int = 1024

    # ── 多模态附件 ────────────────────────────────────────────────────────
    CHAT_MAX_ATTACHMENTS: int = 4
    # 三档上限分开配置：图片小、视频大，用同一个阈值会误伤视频或放宽图片。
    MEDIA_MAX_IMAGE_MB: int = 10
    MEDIA_MAX_AUDIO_MB: int = 20
    MEDIA_MAX_VIDEO_MB: int = 50
    # 视觉模型独立配置：多数 OpenAI 兼容服务商的对话模型与视觉模型不是同一个。
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
    # 可选自定义域名（CDN / 绑定域名）；留空时按 {bucket}.{endpoint} 拼默认公网地址。
    OSS_DOMAIN: str = ""

    # 强制模拟模式开关：置 true 时不调用真实模型，便于无 Key 环境演示与联调。
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

    # ── RAG 检索参数（调参入口见 README「配置说明」） ─────────────────────
    RAG_ENABLED: bool = True
    RAG_TOP_K: int = 5
    RAG_RECALL_K: int = 20
    # 双门槛之一：向量余弦相似度下限；低于它且关键词命中不足则丢弃该片段。
    RAG_MIN_SIM: float = 0.30
    # 双门槛之二：关键词命中词数下限（随查询词数按比例抬升，见 lexicon.LexIndex.search）。
    RAG_LEX_MIN_TERMS: int = 2
    # 融合权重：越大越偏向量通道，0.85 表示以向量为主、关键词做兜底修正。
    RAG_FUSION_ALPHA: float = 0.85
    # 物种不符时的处理：true 直接过滤，false 按系数降权后保留（默认降权，避免误杀通用文档）。
    RAG_SPECIES_STRICT: bool = False
    RAG_SPECIES_PENALTY: float = 0.5
    # 多轮追问改写：短句或含指代词的追问先让 LLM 改写成自包含问句再检索。
    RAG_QUERY_REWRITE: bool = True
    RAG_REWRITE_MIN_CHARS: int = 12

    # ── 知识库导入与切分 ──────────────────────────────────────────────────
    # 留空时回落到 app/knowledge（见 knowledge_dir 属性）。
    KNOWLEDGE_DIR: str = ""
    # 切分三档：目标长度 / 合并下限（过短小节并入上一节）/ 硬上限（超长句强制截断）。
    KB_CHUNK_SIZE: int = 600
    KB_CHUNK_MIN: int = 80
    KB_CHUNK_MAX: int = 900
    EMBED_BATCH_SIZE: int = 10
    KB_MAX_UPLOAD_MB: int = 20
    # 上传原文的留存目录，留空回落到 data/kb（供预览重解析与原文下载）。
    KB_STORAGE_DIR: str = ""
    # 不走网关直连本服务跑运维时的备选凭证，与 ADMIN 角色二选一。
    KB_ADMIN_TOKEN: str = ""
    # Skill 技能包根目录（标准 Agent Skills 范式：每个技能一个 <name>/SKILL.md 包），
    # 留空回落到包内 skills/（见 skills_dir 属性）。
    SKILLS_DIR: str = ""

    # ── 下游业务服务发现（ai-service 直连实例，注入内部 X-User-Id） ───────
    # 服务间身份令牌：随每次下游调用以 X-Internal-Token 头发送，业务侧校验通过后
    # 才信任请求里的 X-User-Id。与单体版的 auth.internal-token（环境变量 INTERNAL_TOKEN）
    # 必须是同一个值；留空则退回「按来源 IP 信任」的旧通道，跨主机 / 容器部署时必然失效。
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
    # 实例列表缓存秒数；刷新失败时使用旧列表，避免 Nacos 抖动放大为业务故障。
    SERVICE_DISCOVERY_CACHE_TTL: float = 10.0
    HTTP_TIMEOUT: float = 8.0

    # ── 结果缓存 TTL（秒） ────────────────────────────────────────────────
    REVIEW_SUMMARY_TTL: int = 21600
    RECOMMEND_CACHE_TTL: int = 300

    # 送入 LLM 的上下文窗口条数（从 checkpoint 记忆里裁剪最近 N 条）。
    HISTORY_MAX_MESSAGES: int = 40

    # ── 长期记忆 ──────────────────────────────────────────────────────────
    MEMORY_ENABLED: bool = True
    # 单轮注入 Prompt 的记忆条数上限，同时也是召回条数上限。
    MEMORY_MAX_ITEMS: int = 20

    # ── 自主 Agent（AgentLoop） ────────────────────────────────────────────
    # 单轮对话内最多允许「模型 → 工具」循环几次。超限后强制收敛为一次无工具作答，
    # 防止模型反复查同一件事把成本与延迟拉爆；3 轮足够覆盖「先查档案再查订单」这类组合问题。
    AGENT_MAX_STEPS: int = 3
    # 启用的工具分组（逗号分隔），未知分组会被忽略。关掉某组等价于让模型「看不见」这些工具，
    # 比在提示词里劝它别调用更可靠。
    # 只读：knowledge / skill（技能正文按需加载，始终启用）/ pet / shop / community / profile
    # 写操作：cart 购物车 · interaction 点赞 · pet_edit 档案维护（可撤销）
    #         publish 发布动态（默认开启；每一次都走人工审批，见下方 AGENT_APPROVAL_TOOLS）
    #         content 评论（默认关闭，公开发布且暂无审批）
    # 注意：下单与支付**不是可配置分组**——交易闭环不开放给模型，AI 的电商能力止于加购。
    AGENT_TOOL_GROUPS: str = "knowledge,skill,pet,shop,cart,community,interaction,pet_edit,publish,profile"
    # 需要人工审批（Human-in-the-loop）的工具，逗号分隔。命中时图会在工具执行前挂起，
    # 由前端弹审批卡片、用户确认后才继续；依赖 checkpoint 保存现场，无 checkpointer 时直接拒绝执行。
    # 默认只保护公开发布；交易类工具已整体下线，不在候选列表里。
    AGENT_APPROVAL_TOOLS: str = "publish_post"

    # ── AI 扩展（管理员在后台配置的 MCP 服务与 Skill 技能） ────────────────
    # MCP 总开关：置 false 时即使库里有启用中的服务也不装配远程工具（Skill 不受此开关影响）。
    AGENT_MCP_ENABLED: bool = True
    # 单个 MCP 服务的连接 / 列工具超时（秒）：不可达的服务只拖累装配阶段，不拖死对话。
    MCP_LOAD_TIMEOUT: float = 10.0
    # 扩展配置进程内缓存 TTL（秒）：管理接口写入会显式失效，TTL 兜底多 worker 部署的跨进程刷新。
    MCP_CACHE_TTL: int = 60

    # ── 生成参数 ──────────────────────────────────────────────────────────
    LLM_MAX_TOKENS: int = 512
    LLM_TEMPERATURE: float = 0.8

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        # 忽略 .env 里多余的历史变量，避免升级时因残留键启动失败。
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _fallback_to_deepseek(self) -> "Settings":
        """把旧版 ``DEEPSEEK_*`` 配置回落到通用的 ``LLM_*`` 配置上。

        历史部署只配了 DEEPSEEK_*，而代码已统一按 LLM_* 读取；这里做一次性补全，
        使两种配置方式都能生效，避免升级后旧 .env 直接失效。

        Returns:
            补全后的自身实例（pydantic 校验链要求返回 self）。

        Raises:
            无。
        """
        if not self.LLM_API_KEY:
            self.LLM_API_KEY = self.DEEPSEEK_API_KEY
        if not self.LLM_BASE_URL:
            self.LLM_BASE_URL = self.DEEPSEEK_BASE_URL
        if not self.LLM_MODEL:
            self.LLM_MODEL = self.DEEPSEEK_MODEL
        # 视觉通道未单独配置时复用主对话模型的凭证——同源服务商通常是同一把 Key。
        if not self.LLM_VISION_API_KEY:
            self.LLM_VISION_API_KEY = self.LLM_API_KEY
        if not self.LLM_VISION_BASE_URL:
            self.LLM_VISION_BASE_URL = self.LLM_BASE_URL
        return self

    @property
    def is_mock(self) -> bool:
        """是否运行在模拟模式。

        缺少 Key 也判定为 mock（而不是启动即崩），目的是让服务在无凭证环境下依然可跑通
        全链路，配合启动日志里的显式告警提示运维补配置。
        """
        return self.MOCK_CHAT or not self.LLM_API_KEY.strip()

    @property
    def llm_enabled(self) -> bool:
        """是否接入了真实对话模型（``is_mock`` 的反向语义，便于调用点正向表达）。"""
        return not self.is_mock

    @property
    def embedding_enabled(self) -> bool:
        """Embedding 是否可用。

        同时要求 Key 与模型名：只配 Key 不配模型时请求必然失败，提前判空比让检索链路
        抛远程错误更容易定位。
        """
        return bool(self.EMBEDDING_API_KEY.strip() and self.EMBEDDING_MODEL.strip())

    @property
    def vision_enabled(self) -> bool:
        """多模态看图能力是否可用（mock 模式下恒为 false，避免走真实请求）。"""
        return (not self.is_mock) and bool(self.LLM_VISION_MODEL.strip()
                                           and self.LLM_VISION_API_KEY.strip()
                                           and self.LLM_VISION_BASE_URL.strip())

    @property
    def asr_enabled(self) -> bool:
        """语音转写能力是否可用；不可用时音频只做占位说明，不影响文字对话。"""
        return bool(self.ASR_API_KEY.strip() and self.ASR_BASE_URL.strip() and self.ASR_MODEL.strip())

    @property
    def oss_configured(self) -> bool:
        """附件存储是否配置完整（四项缺一不可，缺项时上传会明确报错而非静默失败）。"""
        return bool(self.OSS_ENDPOINT.strip() and self.OSS_ACCESS_KEY_ID.strip()
                    and self.OSS_ACCESS_KEY_SECRET.strip() and self.OSS_BUCKET_NAME.strip())

    @property
    def pg_dsn(self) -> str:
        """psycopg 直连串（同步驱动 / 连接池使用）。"""
        return (f"postgresql://{self.PG_USER}:{self.PG_PASSWORD}"
                f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}")

    @property
    def pg_conn_str(self) -> str:
        """SQLAlchemy 风格的连接串（仅评测脚本里的旧 PGVector 集合使用）。"""
        return (f"postgresql+psycopg://{self.PG_USER}:{self.PG_PASSWORD}"
                f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}")

    @property
    def knowledge_dir(self) -> Path:
        """仓库语料目录；未显式配置时取包内 ``app/knowledge``。"""
        raw = (self.KNOWLEDGE_DIR or "").strip()
        return Path(raw) if raw else BASE_DIR / "app" / "knowledge"

    @property
    def kb_storage_dir(self) -> Path:
        """上传原文的留存目录；未显式配置时取 ``data/kb``（与仓库语料分开，便于区分来源）。"""
        raw = (self.KB_STORAGE_DIR or "").strip()
        return Path(raw) if raw else BASE_DIR / "data" / "kb"

    @property
    def skills_dir(self) -> Path:
        """Skill 技能包根目录；未显式配置时取包内 ``skills/``。"""
        raw = (self.SKILLS_DIR or "").strip()
        return Path(raw) if raw else BASE_DIR / "skills"


# 进程级单例：所有模块统一 ``from app.config import settings`` 复用同一份配置。
settings = Settings()
