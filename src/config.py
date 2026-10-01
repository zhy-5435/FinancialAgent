"""全局共享路径配置：数据目录、数据库路径，跨 L1 / L2 / L3 层引用"""
import os
# 配置 HuggingFace 国内镜像（Embedding 模型首次加载时下载）
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

# 确保目录存在（首次运行时自动创建）
for _path in [DATA_DIR, RAW_DATA_DIR, DB_DIR, DB_DIR / "sqlite", DB_DIR / "milvus", DATA_DIR / "excel"]:
    _path.mkdir(parents=True, exist_ok=True)
