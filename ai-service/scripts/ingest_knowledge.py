"""知识库导入脚本（RAG 语料 -> pgvector）

用法（在 ai-service 目录下执行）：
    .venv\\Scripts\\python -m scripts.ingest_knowledge

流程：
  1. 读取 app/knowledge/*.md 全部知识文档；
  2. 用 RecursiveCharacterTextSplitter 按中文标点切分为语义片段；
  3. 清空既有集合后重新写入 pgvector（幂等：可反复执行，以文件为唯一事实来源）。

前置：PostgreSQL 已安装 pgvector 扩展；.env 已配置 EMBEDDING_API_KEY/BASE_URL/MODEL。
Embedding 未配置或向量库不可用时脚本报错退出（RAG 只有 pgvector 一条链路，不做关键词降级）。
"""
import asyncio
import sys
from pathlib import Path

# 支持 `python scripts/ingest_knowledge.py` 直接运行：把包根目录补进 sys.path
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from app import vectorstore  # noqa: E402
from app.config import settings  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402

# 文案来源目录
KNOWLEDGE_DIR = _PACKAGE_ROOT / "app" / "knowledge"

# 中文友好的分隔符优先级：先段落、再句子、最后标点
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def load_documents() -> list:
    """读取全部知识文件，返回 [{source, category, content}]"""
    docs = []
    for md in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = md.read_text(encoding="utf-8").strip()
        if text:
            docs.append({"source": md.name, "category": md.stem, "content": text})
    return docs


async def main() -> None:
    setup_logging()
    docs = load_documents()
    if not docs:
        print(f"[错误] 未找到知识文件：{KNOWLEDGE_DIR}")
        sys.exit(1)

    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=280,          # 片段长度：适配中文，约 1-2 段
        chunk_overlap=40,        # 相邻片段重叠，避免切断语义
        separators=_SEPARATORS,
    )

    texts, metadatas = [], []
    for doc in docs:
        for i, chunk in enumerate(splitter.split_text(doc["content"])):
            chunk = chunk.strip()
            if len(chunk) < 10:
                continue
            texts.append(chunk)
            metadatas.append({
                "source": doc["source"],
                "category": doc["category"],
                "chunk_index": i,
            })

    print(f"知识文件 {len(docs)} 个，切分片段 {len(texts)} 条，开始写入 pgvector（集合 {settings.RAG_COLLECTION}）...")

    try:
        await vectorstore.aclear()   # 幂等：先清空集合内旧向量，再全量写入
        written = await vectorstore.aadd_texts(texts, metadatas)
    except Exception as exc:
        print(f"[错误] 写入 pgvector 失败：{exc}")
        sys.exit(1)
    print(f"[完成] 成功写入 {written} 条知识片段到 pgvector。")


if __name__ == "__main__":
    asyncio.run(main())
