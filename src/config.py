"""全局共享路径配置：数据目录、数据库路径，跨 L1 / L2 / L3 层引用"""
import os
# 配置 HuggingFace 国内镜像（Embedding 模型首次加载时下载）
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from pathlib import Path

# 若 BGE 模型已在本地缓存，则强制离线加载：跳过 huggingface_hub 对镜像的逐文件联网校验，
# 避免镜像缓慢/不可达时首启长时间静默阻塞（表现为运行 pipeline 后控制台空白、一直转圈）。
# 全新机器无缓存时不置此标志，仍会走上面的镜像正常下载一次。
_hf_cache_home = Path(os.getenv("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
_hub_cache_dir = Path(os.getenv("HUGGINGFACE_HUB_CACHE") or (_hf_cache_home / "hub"))
_bge_cache_dir = _hub_cache_dir / "models--BAAI--bge-base-zh-v1.5"
if _bge_cache_dir.exists():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

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
