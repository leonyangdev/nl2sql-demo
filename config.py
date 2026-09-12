"""全局配置。

三条"数据库"各司其职，是整个架构最重要的边界：

    sales.db        业务数据库   -> 业务数据真相源（Source of Truth）
    metadata.db     元数据中心   -> 元数据真相源（Source of Truth）
    vector_store.db 向量库       -> 语义检索索引（Search Index，可随时重建）

所有路径都基于本文件所在目录计算，保证从任意工作目录运行结果一致。

环境变量在**导入本模块时**一次性装配完毕：
默认 **.env 优先于进程环境变量**（NL2SQL_DOTENV_OVERRIDE=0 可改回经典行为）。

为什么这里要"文件优先"？因为机器上很容易残留一个过期的全局同名变量，
它会静默遮蔽项目 .env 里正确的 key，最后表现为莫名其妙的 401。
所以：项目里的 .env 是本项目最具体的配置，应当赢；并且覆盖行为会被记录下来，
启动时打印出来，让这类问题一眼可见。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# .env 加载（不引入 python-dotenv，几十行搞定）
# ---------------------------------------------------------------------------
def _looks_like_placeholder(value: str) -> bool:
    """识别 .env.example 里那类占位符，避免它们覆盖真实配置。"""
    text = value.strip()
    if not text:
        return True
    if re.fullmatch(r"<.*>", text):                       # <your-key>
        return True
    if re.fullmatch(r"sk-x+", text, re.IGNORECASE):       # sk-xxxxxxx
        return True
    if re.match(r"^(your|my|xxx|todo|changeme|placeholder|example)\b", text, re.I):
        return True
    if len(text) >= 8 and len(set(text)) <= 2:            # xxxxxxxx / 00000000
        return True
    return False


@dataclass
class DotenvReport:
    path: Path
    exists: bool = False
    loaded: list[str] = field(default_factory=list)             # 生效来自 .env 的键
    overridden: list[str] = field(default_factory=list)         # 覆盖掉进程环境变量的键
    skipped_placeholder: list[str] = field(default_factory=list)


def load_dotenv(path: Path | None = None, override: bool | None = None) -> DotenvReport:
    """把 .env 读进 os.environ，返回一份加载报告（只含键名与长度，不含值）。"""
    env_path = path or (BASE_DIR / ".env")
    report = DotenvReport(path=env_path)

    if not env_path.exists():
        return report
    report.exists = True

    if override is None:
        # 默认 .env 优先；设 NL2SQL_DOTENV_OVERRIDE=0 恢复"进程环境变量优先"
        override = (os.getenv("NL2SQL_DOTENV_OVERRIDE") or "1").strip().lower() not in (
            "0", "false", "no", "off",
        )

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()

        # 行内注释：只有"未加引号"且 # 前面有空白时才算注释。
        # 值里本身带 # 的（比如密码）请用引号包起来。
        #
        # 这个细节很要紧，否则 a=b   # 备注 会把 "b   # 备注" 整串当成值，
        # 最后表现为莫名其妙的 404 / 401。
        quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
        if not quoted:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if not key:
            continue

        if _looks_like_placeholder(value):
            report.skipped_placeholder.append(key)
            continue

        existing = os.environ.get(key)
        if existing is not None and existing != value:
            if not override:
                continue
            report.overridden.append(
                f"{key}（进程环境变量 {len(existing)} 字符 -> .env {len(value)} 字符）"
            )

        os.environ[key] = value
        if key not in report.loaded:
            report.loaded.append(key)

    return report


DOTENV_PATH = BASE_DIR / ".env"
DOTENV_REPORT = load_dotenv(DOTENV_PATH)
# 兼容旧写法
DOTENV_LOADED_KEYS = DOTENV_REPORT.loaded


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


# ---------------------------------------------------------------------------
# 业务数据库：存"订单、商品、区域、金额"这类业务事实
# ---------------------------------------------------------------------------
SOURCE_DB_PATH = BASE_DIR / "sales.db"
SOURCE_DB_URL = _sqlite_url(SOURCE_DB_PATH)

# ---------------------------------------------------------------------------
# 元数据中心：存"关于业务数据的知识"（表/字段/关系/指标/取值）
# ---------------------------------------------------------------------------
METADATA_DB_PATH = BASE_DIR / "metadata.db"
METADATA_DB_URL = _sqlite_url(METADATA_DB_PATH)

# ---------------------------------------------------------------------------
# 向量库：只负责"按语义找元数据"，找到后回元数据中心取完整定义
# ---------------------------------------------------------------------------
VECTOR_STORE_PATH = BASE_DIR / "vector_store.db"
COLLECTION_NAME = "nl2sql_metadata_index"

# 数据源标识：以后接 PostgreSQL / MySQL，只改上面的 URL
DATASOURCE_NAME = "sales_db"
SCHEMA_NAME = "main"

# ---------------------------------------------------------------------------
# Embedding
#   auto      配了 API Key 就用线上 Embedding，否则退回本地 hashing embedding
#             （线上接口临时不可用时也会自动降级，不会让整条链路挂掉）
#   dashscope 强制使用线上 Embedding API，失败就直接报错
#   hash      强制使用本地 n-gram 哈希向量（零依赖、离线）
# ---------------------------------------------------------------------------
EMBEDDER_MODE = (os.getenv("NL2SQL_EMBEDDER") or "auto").strip().lower()

# 线上 Embedding 服务（OpenAI 兼容的 /embeddings 接口）
# 默认复用 DashScope 的配置，也可以用 NL2SQL_EMBEDDING_* 单独指定
EMBEDDING_API_KEY_VARS = ("NL2SQL_EMBEDDING_API_KEY", "DASHSCOPE_API_KEY")
EMBEDDING_BASE_URL_VARS = (
    "NL2SQL_EMBEDDING_BASE_URL",
    "DASHSCOPE_BASE_URL",
)
EMBEDDING_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_MODEL = (
    os.getenv("NL2SQL_EMBEDDING_MODEL")
    or os.getenv("DASHSCOPE_EMBEDDING_MODEL")
    or "text-embedding-v4"
)
# 期望维度。设为 0 表示"不传 dimensions 参数，完全由服务端决定"
EMBEDDING_DIM = int(os.getenv("NL2SQL_EMBEDDING_DIM") or 1024)
# 单次请求最多带几条文本（DashScope 建议 <= 10）
EMBEDDING_BATCH_SIZE = int(os.getenv("NL2SQL_EMBEDDING_BATCH_SIZE") or 10)
EMBEDDING_TIMEOUT = int(os.getenv("NL2SQL_EMBEDDING_TIMEOUT") or 60)

# 本地兜底用的哈希向量维度
HASH_EMBEDDING_DIM = 1024

# ---------------------------------------------------------------------------
# 向量库后端
#   local  : SQLite + 暴力检索（零依赖，默认）
#   milvus : 真实 Milvus（需 docker 起服务 + pip install pymilvus）
# ---------------------------------------------------------------------------
VECTOR_STORE_BACKEND = os.getenv("NL2SQL_VECTOR_STORE", "local")
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")

# ---------------------------------------------------------------------------
# 检索参数
# ---------------------------------------------------------------------------
TOP_K = 20          # 向量检索候选数量
PER_TYPE_TOP_K = 3  # 每类实体（table/column/metric/value）最多保留几条
MIN_SCORE = 0.0     # 相似度下限

# 低基数字段自动采样成 Value Metadata 的上限，超过就认为是高基数，不采样
MAX_ENUM_VALUES = 20

# ---------------------------------------------------------------------------
# 在线 SQL 生成
#   llm  : 把元数据上下文交给大模型生成 SQL（默认，需配置 API Key）
#   rule : 用本地规则 + 槽位填充生成 SQL（不依赖大模型，可离线）
# ---------------------------------------------------------------------------
SQL_GENERATOR = (os.getenv("NL2SQL_SQL_GENERATOR") or "llm").lower()
LLM_SUMMARY = (os.getenv("NL2SQL_LLM_SUMMARY") or "1").lower() not in (
    "0", "false", "no",
)
# 大模型生成/修正 SQL 的最大尝试次数（含首次）
LLM_MAX_ATTEMPTS = int(os.getenv("NL2SQL_LLM_MAX_ATTEMPTS") or 3)
# 总结时最多回传给大模型多少行
LLM_SUMMARY_MAX_ROWS = int(os.getenv("NL2SQL_LLM_SUMMARY_MAX_ROWS") or 50)
