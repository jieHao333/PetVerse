"""知识库子模块（RAG 语料管线）

documents  解析 md / pdf / docx / doc -> 小节结构；
chunking   小节感知切分 -> 带上下文标题的知识片段；
store      PostgreSQL + pgvector 存储（kb_document / kb_chunk / kb_meta）；
lexicon    jieba + BM25 关键词检索通道；
search     混合检索（稠密 + 关键词，RRF 融合、物种过滤、召回门槛）；
ingest     解析 → 切分 → 向量化 → 入库 的编排（导入脚本与上传接口共用）。
"""