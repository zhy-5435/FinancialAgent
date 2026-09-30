import os
# 配置 HuggingFace 国内镜像
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 数据路径
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
EXCEL_PATH = DATA_DIR / "excel" / "L1_pro.xlsx"

# 数据库路径
DB_DIR = PROJECT_ROOT / "db"
SQLITE_DB_PATH = DB_DIR / "sqlite" / "l1_core.db"
MILVUS_DB_PATH = DB_DIR / "milvus" / "l1_milvus.db"

# 向量库配置
MILVUS_COLLECTION = "l1_core_knowledge"
EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
VECTOR_DIM = 512  # bge-small-zh-v1.5 固定输出维度为512
TOP_K = 3

# 确保目录存在
for path in [DATA_DIR, RAW_DATA_DIR, DB_DIR, DB_DIR/"sqlite", DB_DIR/"milvus", DATA_DIR/"excel"]:
    path.mkdir(parents=True, exist_ok=True)
