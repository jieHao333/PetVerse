"""知识库（RAG）子系统。

把 md / pdf / docx / doc 语料解析、切分、向量化后写入 PostgreSQL(pgvector)，
并在提问时做「向量 + BM25 关键词」双通道混合召回。
"""
