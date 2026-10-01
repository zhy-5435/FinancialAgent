"""L1 数据层专有配置：向量库 Collection、Embedding 模型与检索参数"""

MILVUS_COLLECTION = "l1_core_knowledge"
EMBEDDING_MODEL_NAME = "BAAI/bge-base-zh-v1.5"
VECTOR_DIM = 768  # bge-base-zh-v1.5 固定输出维度
TOP_K = 3         # 默认返回切片数

# 双路混合检索（向量语义召回 + SQLite FTS5 关键词召回）
CANDIDATE_TOP_K = 10  # 每路召回的候选切片数，两路去重合并后再截取 TOP_K
# 权重经 14 条评测集网格搜索校准：两路得分先各自 min-max 归一化再加权，
# 关键词路作排序辅助信号、不宜主导（孪生规则文本关键词得分易达 1.0），故降权至 0.2。
VECTOR_WEIGHT = 0.8   # 综合得分中归一化向量相似度的权重
KEYWORD_WEIGHT = 0.2  # 综合得分中归一化关键词得分的权重
