"""知识库导入命令行工具。

职责：把 ``app/knowledge``（或指定目录 / 单个文件）的语料解析、切分、向量化后写入
PostgreSQL(pgvector)。支持增量导入、强制重导、全量重建与规模查看。

主要导出：无（作为脚本执行）。

外部依赖：``app.kb.*``、``app.config.settings``、``app.logging_setup``。

使用示例：
    python -m scripts.ingest_knowledge              # 增量导入（内容未变则跳过）
    python -m scripts.ingest_knowledge --full       # 清空后全量重建
    python -m scripts.ingest_knowledge --force      # 不跳过，全部重新向量化
    python -m scripts.ingest_knowledge --file x.pdf
    python -m scripts.ingest_knowledge --stats      # 只看规模与文档清单

退出码：0 成功 / 1 部分文件失败 / 2 参数或环境错误（便于 CI 与运维脚本判断）。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 使脚本可直接执行（python scripts/ingest_knowledge.py）时也能 import app 包。
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from app.config import settings  # noqa: E402
from app.kb import ingest, lexicon, store  # noqa: E402
from app.kb.documents import SUPPORTED_SUFFIXES  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。

    Returns:
        argparse 命名空间。

    Raises:
        SystemExit: 参数非法时由 argparse 抛出。
    """
    parser = argparse.ArgumentParser(description="知识库导入（md/pdf/docx/doc → pgvector）")
    parser.add_argument("--full", action="store_true", help="清空知识库后全量重建")
    parser.add_argument("--force", action="store_true", help="忽略内容哈希，全部重新向量化")
    parser.add_argument("--file", type=Path, default=None, help="只导入这一个文件")
    parser.add_argument("--dir", type=Path, default=None, help="指定语料目录（默认取 KNOWLEDGE_DIR）")
    parser.add_argument("--limit", type=int, default=None, help="只导入前 N 个文件（调试用）")
    parser.add_argument("--stats", action="store_true", help="打印当前知识库规模后退出")
    return parser.parse_args()


async def main() -> int:
    """脚本主流程。

    Returns:
        进程退出码：0 全部成功 / 1 有文件导入失败 / 2 参数或环境错误。

    Raises:
        无——异常统一转成退出码（``finally`` 保证连接池关闭）。
    """
    args = _parse_args()
    setup_logging()

    await store.init()
    try:
        if args.stats:
            stats = await store.stats()
            print(f"知识库：文档 {stats['documents']} 篇 / 片段 {stats['chunks']} 条 / "
                  f"正文 {stats['chars']} 字")
            # 只取前 100 篇：这是给人看的概览，不是导出功能。
            documents, _total = await store.list_documents(page=1, size=100)
            for doc in documents:
                print(f"  - {doc['docId']:<28} {doc['kind']:<5} "
                      f"{doc['chunkCount']:>3} 片段  {doc['name']}")
            return 0

        directory = args.dir or settings.knowledge_dir
        if args.file:
            path = Path(args.file)
            # 提前校验后缀：解析器也会校验，但这里能给出一句更友好的 CLI 提示。
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                print(f"[错误] 不支持的格式 {path.suffix}，支持：{'/'.join(SUPPORTED_SUFFIXES)}")
                return 2
            # 指定单文件时隐含「我就是要更新它」，因此强制重导。
            result = await ingest.ingest_path(path, origin="repo", force=True)
            print(f"[完成] {result['source']} → {result['chunks']} 条片段（doc_id={result['docId']}）")
            stats = await store.stats()
            print(f"知识库现有文档 {stats['documents']} 篇 / 片段 {stats['chunks']} 条")
            return 0

        if not directory.is_dir():
            print(f"[错误] 未找到语料目录：{directory}")
            return 2

        if args.full:
            # 全量重建：先清空再导入，保证库里不存在已从仓库删除的旧文档。
            removed = await store.clear_all()
            print(f"[全量重建] 已清空知识库（原 {removed} 篇文档）")

        print(f"开始扫描 {directory}（{'全量重建' if args.full else '增量导入'}，"
              f"格式 {'/'.join(SUPPORTED_SUFFIXES)}）...")
        # --full 时同样要 force：清空后若仍按哈希跳过，会得到一个空库。
        summary = await ingest.ingest_directory(directory, force=args.force or args.full,
                                                limit=args.limit)

        print(f"扫描 {summary['scanned']} 个文件：导入 {summary['ingested']} 篇"
              f"（新增/变更，共 {summary['chunks']} 条片段），跳过 {summary['skipped']} 篇（内容未变）")
        for failure in summary["failures"]:
            print(f"[失败] {failure['file']}：{failure['reason']}")
        print(f"知识库当前：文档 {summary['totalDocuments']} 篇 / 片段 {summary['totalChunks']} 条")

        # 主动构建一次 BM25 索引：既验证 jieba 是否可用，也让运维立刻看到索引规模。
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
        # 无论成功失败都要关池，否则脚本可能因连接未释放而挂住不退出。
        await store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
