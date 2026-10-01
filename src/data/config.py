"""L1 数据层专有配置：向量库 Collection、Embedding 模型与检索参数"""

MILVUS_COLLECTION = "l1_core_knowledge"
EMBEDDING_MODEL_NAME = "BAAI/bge-base-zh-v1.5"
VECTOR_DIM = 768  # bge-base-zh-v1.5 固定输出维度
TOP_K = 3         # 默认返回切片数

# 双路混合检索（向量语义召回 + SQLite FTS5 关键词召回）
CANDIDATE_TOP_K = 10  # 每路召回的候选切片数，两路去重合并后再截取 TOP_K
VECTOR_WEIGHT = 0.7   # 综合得分中向量相似度的权重
KEYWORD_WEIGHT = 0.3  # 综合得分中关键词得分的权重
