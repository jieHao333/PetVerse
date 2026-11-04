"""知识库（RAG）子系统。

职责：把 md / pdf / docx / doc 语料解析、切分、向量化后写入 PostgreSQL(pgvector)，
并在提问时做「向量 + BM25 关键词」双通道混合召回。

主要导出：
    - ``documents`` 文档解析（按格式抽取标题层级与正文，产出 ``ParsedDocument``）
    - ``chunking``  片段切分（按小节合并 / 按句切分 / 表格整块保留）
    - ``store``     pgvector 存储（kb_document / kb_chunk 读写、向量检索）
    - ``lexicon``   jieba 分词与进程内 BM25 索引
    - ``ingest``    入库编排（解析 → 切分 → 向量化 → 覆盖写入 → 索引失效）
    - ``search``    检索编排（双通道召回 → 序值融合 → 物种过滤 → 双门槛筛选）

外部依赖：``psycopg`` / ``psycopg_pool``、pgvector 扩展、``jieba``、``python-docx``、
``PyMuPDF``，以及 ``app.llm.get_embeddings`` 提供的 Embedding 客户端。

使用示例：
    from app.kb import ingest, search

    await ingest.ingest_path(Path("app/knowledge/犬猫疫苗与免疫程序.md"))
    hits = await search.retrieve("幼猫什么时候打疫苗", pet_species=["猫"])
"""
