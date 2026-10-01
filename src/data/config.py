"""L1 数据层专有配置：向量库 Collection、Embedding 模型与检索参数"""

MILVUS_COLLECTION = "l1_core_knowledge"
EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
VECTOR_DIM = 512  # bge-small-zh-v1.5 固定输出维度
TOP_K = 3         # 默认返回切片数
