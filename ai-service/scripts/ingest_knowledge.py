"""知识库导入脚本（md / pdf / docx / doc → pgvector + BM25 词表）

用法（在 ai-service 目录下执行）：
    python -m scripts.ingest_knowledge              # 增量导入：内容未变更的文档跳过
    python -m scripts.ingest_knowledge --full       # 清空知识库后全量重建
    python -m scripts.ingest_knowledge --force      # 不跳过，全部重新切分并向量化
    python -m scripts.ingest_knowledge --file x.pdf # 只导入指定文件（任意受支持格式）
    python -m scripts.ingest_knowledge --stats      # 只看当前知识库规模

流程（实现在 app/kb/ingest.py，与上传接口共用）：
  1. 扫描 settings.knowledge_dir 下全部受支持文件；
  2. 解析成「标题层级 + 小节」，按小节切成带上下文标题的片段；
  3. 片段正文向量化，同时生成 jieba 分词（含标题/分类/标签扩展词）；
  4. 按 doc_id 覆盖写入 kb_document / kb_chunk，并自增 BM25 版本戳。

前置：PostgreSQL 可连且已安装 pgvector；.env 已配置 EMBEDDING_API_KEY/BASE_URL/MODEL。
Embedding 未配置或向量库不可用时脚本报错退出（RAG 不做关键词降级）。
"""
import argparse
import asyncio
import sys
from pathlib import Path

# 支持 `python scripts/ingest_knowledge.py` 直接运行：把包根目录补进 sys.path
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from app.config import settings  # noqa: E402
from app.kb import ingest, lexicon, store  # noqa: E402
from app.kb.documents import SUPPORTED_SUFFIXES  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="知识库导入（md/pdf/docx/doc → pgvector）")
    parser.add_argument("--full", action="store_true", help="清空知识库后全量重建")
    parser.add_argument("--force", action="store_true", help="忽略内容哈希，全部重新向量化")
    parser.add_argument("--file", type=Path, default=None, help="只导入这一个文件")
    parser.add_argument("--dir", type=Path, default=None, help="指定语料目录（默认取 KNOWLEDGE_DIR）")
    parser.add_argument("--limit", type=int, default=None, help="只导入前 N 个文件（调试用）")
    parser.add_argument("--stats", action="store_true", help="打印当前知识库规模后退出")
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    setup_logging()

    await store.init()
    try:
        if args.stats:
            stats = await store.stats()
            print(f"知识库：文档 {stats['documents']} 篇 / 片段 {stats['chunks']} 条 / "
                  f"正文 {stats['chars']} 字")
            # 列表接口是分页的，这里取满一页上限看全量（文档数远超 100 篇时改用管理页翻查）
            documents, _total = await store.list_documents(page=1, size=100)
            for doc in documents:
                print(f"  - {doc['docId']:<28} {doc['kind']:<5} "
                      f"{doc['chunkCount']:>3} 片段  {doc['name']}")
            return 0

        directory = args.dir or settings.knowledge_dir
        if args.file:
            path = Path(args.file)
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                print(f"[错误] 不支持的格式 {path.suffix}，支持：{'/'.join(SUPPORTED_SUFFIXES)}")
                return 2
            result = await ingest.ingest_path(path, origin="repo", force=True)
            print(f"[完成] {result['source']} → {result['chunks']} 条片段（doc_id={result['docId']}）")
            stats = await store.stats()
            print(f"知识库现有文档 {stats['documents']} 篇 / 片段 {stats['chunks']} 条")
            return 0

        if not directory.is_dir():
            print(f"[错误] 未找到语料目录：{directory}")
            return 2

        if args.full:
            removed = await store.clear_all()
            print(f"[全量重建] 已清空知识库（原 {removed} 篇文档）")

        print(f"开始扫描 {directory}（{'全量重建' if args.full else '增量导入'}，"
              f"格式 {'/'.join(SUPPORTED_SUFFIXES)}）...")
        summary = await ingest.ingest_directory(directory, force=args.force or args.full,
                                                limit=args.limit)

        print(f"扫描 {summary['scanned']} 个文件：导入 {summary['ingested']} 篇"
              f"（新增/变更，共 {summary['chunks']} 条片段），跳过 {summary['skipped']} 篇（内容未变）")
        for failure in summary["failures"]:
            print(f"[失败] {failure['file']}：{failure['reason']}")
        print(f"知识库当前：文档 {summary['totalDocuments']} 篇 / 片段 {summary['totalChunks']} 条")

        # BM25 索引与片段数一起校验：向量写进去了但分词是空的，检索会退化成纯向量，
        # 属于配置/依赖问题（jieba 缺失），必须让导入结果可见而不是静默通过
        index = await lexicon.get_index()
        if index is None or index.total == 0:
            print("[警告] BM25 关键词索引为空，混合检索将只有向量通道生效（检查 jieba 是否安装）")
        else:
            print(f"BM25 关键词索引：片段 {index.total} 条 / 词表 {len(index.postings)} 项")

        if summary["failures"]:
            print(f"[完成但有问题] {len(summary['failures'])} 个文件未导入，请修复后重跑")
            return 1
        return 0
    finally:
        await store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
