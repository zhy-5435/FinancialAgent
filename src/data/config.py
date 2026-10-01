"""L1 数据层专有配置：向量库 Collection、Embedding 模型与检索参数"""
from src.config import PROJECT_ROOT

MILVUS_COLLECTION = "l1_core_knowledge"
EMBEDDING_MODEL_NAME = "BAAI/bge-base-zh-v1.5"
# 本地部署的模型目录（由 HF 缓存经 SentenceTransformer.save() 导出的标准目录）：
# 存在则优先从本地路径加载，完全不依赖 HuggingFace 缓存位置与网络
EMBEDDING_MODEL_DIR = PROJECT_ROOT / "models" / "bge-base-zh-v1.5"
EMBEDDING_MODEL_PATH = str(EMBEDDING_MODEL_DIR) if EMBEDDING_MODEL_DIR.exists() else EMBEDDING_MODEL_NAME
VECTOR_DIM = 768  # bge-base-zh-v1.5 固定输出维度
TOP_K = 3         # 默认返回切片数

# 双路混合检索（向量语义召回 + SQLite FTS5 关键词召回）
CANDIDATE_TOP_K = 10  # 每路召回的候选切片数，两路去重合并后再截取 TOP_K
# 权重经 14 条评测集网格搜索校准：两路得分先各自 min-max 归一化再加权，
# 关键词路作排序辅助信号、不宜主导（孪生规则文本关键词得分易达 1.0），故降权至 0.2。
VECTOR_WEIGHT = 0.8   # 综合得分中归一化向量相似度的权重
KEYWORD_WEIGHT = 0.2  # 综合得分中归一化关键词得分的权重

# 轻量重排序（Reranker）：双路召回得到候选集后，用交叉编码器对候选做二次精排
# 模型体量小（bge-reranker-base，约 1.1GB），本地可 CPU 运行；已在 HF 缓存则离线加载
RERANKER_MODEL_NAME = "BAAI/bge-reranker-base"
# 本地部署目录优先（models/bge-reranker-base），无本地目录时回退 HF 模型名（命中缓存/镜像）
RERANKER_MODEL_DIR = PROJECT_ROOT / "models" / "bge-reranker-base"
RERANKER_MODEL_PATH = str(RERANKER_MODEL_DIR) if RERANKER_MODEL_DIR.exists() else RERANKER_MODEL_NAME
# 默认关闭：14 条评测集实测精排后 Hit@1/Hit@3/Recall@3/MRR@3 全部持平（收益为 0），
# 而每查询多 ~0.8-2.5s、首次加载 ~30s；残留错排源于切片孪生割裂，属数据层问题，精排解决不了。
# 待「切片前置主体名/产品名」数据治理完成、评测集补入疑难查询后再置 True 复测启用。
RERANK_ENABLED = False    # 开启即走交叉编码器精排；模型缺失/加载失败自动降级回双路加权排序
RERANK_CANDIDATE_K = 20   # 参与精排的候选上限（取加权排序后的前 K 条送交叉编码器重排）
