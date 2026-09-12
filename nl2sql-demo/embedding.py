"""Embedding —— 把 Retrieval Document 变成语义向量。

两种实现，接口只有一个 `encode()`，上层 Indexer / Retriever 完全无感：

    DashScopeEmbedder  线上 Embedding 服务（阿里云百炼 text-embedding-v4）
    HashingEmbedder    本地 n-gram 哈希向量（零依赖、离线兜底）

这就是"换 Embedding 模型不影响架构"的含义：`vector_store` 只关心
"给我一串等长、归一化过的 float"，不关心它是谁算出来的。

线上模型和本地哈希向量的差距很大。哈希向量只能捕捉字面/词形重合，
捕捉不到"成交额 ≈ 销售额"这种纯语义等价 —— 那部分靠 business_config
里的 synonyms 补。同一个问题、同一套元数据，实测差距：

    客户买了多少件东西 -> 销量      哈希 0.067   /  线上 0.497
    今年哪个区域卖得最好 -> 销售额   哈希 0.029   /  线上 0.479

所以本地实现只作为"没配 key / 接口不可用"时的兜底，不推荐长期使用。
"""

from __future__ import annotations

import math
import os
import re
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

import config
from http_client import HttpError, post_json

# 把文本切成：连续中文串 / 连续英文数字串
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-z0-9_]+")


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


class BaseEmbedder(ABC):
    name: str = "base"
    dim: int = 0

    @abstractmethod
    def encode(self, texts: list[str]) -> list[list[float]]:
        """把一批文本变成等长向量。

        约定：向量只用于余弦相似度，实现方需要保证已做 L2 归一化
        （向量库里的检索直接算点积）。
        """


# ===========================================================================
# ① 线上 Embedding 服务（阿里云百炼 DashScope，OpenAI 兼容 /embeddings）
# ===========================================================================
@dataclass(frozen=True)
class EmbeddingAPISettings:
    api_key: str
    base_url: str
    model: str
    dim: int          # 0 表示不传 dimensions，由服务端决定
    batch_size: int
    timeout: int

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/embeddings"):
            return base
        return f"{base}/embeddings"

    def describe(self) -> str:
        # 绝不打印 api_key 的内容
        return (
            f"model={self.model} endpoint={self.endpoint} "
            f"api_key=已配置(len={len(self.api_key)})"
        )


def resolve_embedding_api() -> EmbeddingAPISettings | None:
    """从环境变量 / .env 解析线上 Embedding 配置；没有 key 就返回 None。"""
    api_key = _first_env(config.EMBEDDING_API_KEY_VARS)
    if not api_key:
        return None

    model = config.EMBEDDING_MODEL.strip()
    # 模型名里出现空白基本只有一个原因：.env 写了行内注释没被剥掉，
    # 结果整串被当成模型名，服务端只会回一个很迷惑的 404 Model not exist。
    if not model or any(ch.isspace() for ch in model):
        raise RuntimeError(
            f"NL2SQL_EMBEDDING_MODEL 的值不合法：{model!r}\n"
            f"  模型名里不应该有空格。常见原因是 .env 里写了行内注释，例如\n"
            f"      NL2SQL_EMBEDDING_MODEL=text-embedding-v4   # 备选 v3\n"
            f"  正确写法（行内注释前面要有空格是支持的，但建议独占一行）：\n"
            f"      NL2SQL_EMBEDDING_MODEL=text-embedding-v4\n"
        )

    return EmbeddingAPISettings(
        api_key=api_key,
        base_url=(
            _first_env(config.EMBEDDING_BASE_URL_VARS)
            or config.EMBEDDING_DEFAULT_BASE_URL
        ),
        model=config.EMBEDDING_MODEL,
        dim=config.EMBEDDING_DIM,
        batch_size=max(1, config.EMBEDDING_BATCH_SIZE),
        timeout=config.EMBEDDING_TIMEOUT,
    )


class DashScopeEmbedder(BaseEmbedder):
    """阿里云百炼（DashScope）文本向量模型。

    走的是 OpenAI 兼容的 `POST {base}/embeddings`，请求体：

        {"model": "text-embedding-v4", "input": ["文本1", "文本2"], "dimensions": 1024}

    响应体：

        {"data": [{"index": 0, "embedding": [...]}, ...], "usage": {...}}
    """

    def __init__(self, settings: EmbeddingAPISettings):
        self.settings = settings

        # 维度不要硬猜，探测一次（和课程里 sample_vector 的做法一致）
        self.dim = self._probe_dim()
        if settings.dim and settings.dim != self.dim:
            print(
                f"[embedding] 注意：期望维度 {settings.dim}，"
                f"服务端实际返回 {self.dim}，以服务端为准"
            )
        # 模型名会写进向量库元信息：换模型时 indexer 靠它触发全量重建
        self.name = f"dashscope:{settings.model}:{self.dim}"

    # -- 单批请求 ---------------------------------------------------------
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict = {
            "model": self.settings.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.settings.dim:
            payload["dimensions"] = self.settings.dim

        try:
            data = post_json(
                url=self.settings.endpoint,
                payload=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=self.settings.timeout,
            )
        except HttpError as exc:
            raise RuntimeError(f"Embedding 接口调用失败：{exc}") from exc

        items = data.get("data") or []
        if len(items) != len(texts):
            raise RuntimeError(
                f"Embedding 返回条数不符：请求 {len(texts)} 条，返回 {len(items)} 条"
            )

        # 服务端不保证顺序，按 index 排回去
        items = sorted(items, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in items]

    def _probe_dim(self) -> int:
        return len(self._embed_batch(["维度探测"])[0])

    # -- 批量请求 ---------------------------------------------------------
    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batch_size = self.settings.batch_size

        for start in range(0, len(texts), batch_size):
            vectors.extend(self._embed_batch(texts[start : start + batch_size]))

        return vectors


# ===========================================================================
# ② 本地哈希向量（离线兜底）
# ===========================================================================
class HashingEmbedder(BaseEmbedder):
    """字符 n-gram 哈希 + 亚线性 TF + L2 归一化。

    优点：零依赖、离线、可复现、毫秒级，永远不会因为配额/网络挂掉。
    缺点：只认字面重合，不认语义等价（由 business_config 的 synonyms 补）。

    故意保持"每条文本独立计算"—— 不依赖任何全局语料统计，
    所以新增/修改一条元数据只需要重算这一条，增量索引天然成立。
    """

    name = "hash-ngram"

    def __init__(self, dim: int = config.HASH_EMBEDDING_DIM):
        self.dim = dim

    def _features(self, text: str):
        """中文取 2/3-gram，英文数字取整词。"""
        for run in _TOKEN_RE.findall(text.lower()):
            if "\u4e00" <= run[0] <= "\u9fff":
                for n in (2, 3):
                    for i in range(len(run) - n + 1):
                        yield run[i : i + n]
            else:
                yield run

    def _bucket(self, feature: str) -> int:
        return zlib.crc32(feature.encode("utf-8")) % self.dim

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []

        for text in texts:
            vector = [0.0] * self.dim
            counts: dict[int, int] = {}

            for feature in self._features(text):
                bucket = self._bucket(feature)
                counts[bucket] = counts.get(bucket, 0) + 1

            for bucket, count in counts.items():
                # 亚线性 TF：出现 10 次不等于权重是 1 次的 10 倍
                vector[bucket] = 1.0 + math.log(count)

            norm = math.sqrt(sum(v * v for v in vector))
            if norm > 0:
                vector = [v / norm for v in vector]

            vectors.append(vector)

        return vectors


# ===========================================================================
# 工厂
# ===========================================================================
_API_MODES = ("auto", "dashscope", "api", "online", "aliyun", "qwen")
_cache: dict[str, BaseEmbedder] = {}


def _build_dashscope(settings: EmbeddingAPISettings) -> BaseEmbedder:
    embedder = DashScopeEmbedder(settings)
    print(f"[embedding] 使用线上 Embedding：{settings.describe()} dim={embedder.dim}")
    return embedder


def _build_hashing(reason: str = "") -> BaseEmbedder:
    embedder = HashingEmbedder()
    if reason:
        print(f"[embedding] {reason}")
    print(
        f"[embedding] 使用 {embedder.name}（dim={embedder.dim}，本地离线）"
    )
    return embedder


def get_embedder(mode: str | None = None) -> BaseEmbedder:
    """按配置返回 Embedding 实现。

    每个模式最多各建一次实例并缓存 —— 线上模型构造时会探测一次维度，
    缓存能避免每次问答都多打一次接口。
    """
    mode = (mode or config.EMBEDDER_MODE).strip().lower()

    if mode in _cache:
        return _cache[mode]

    embedder: BaseEmbedder

    if mode == "hash":
        embedder = _build_hashing()

    elif mode in _API_MODES:
        settings = resolve_embedding_api()

        if settings is None:
            if mode != "auto":
                raise RuntimeError(
                    f"EMBEDDER={mode} 需要 API Key，但没找到："
                    f"{' / '.join(config.EMBEDDING_API_KEY_VARS)}"
                )
            embedder = _build_hashing("未配置 Embedding API Key，退回本地实现。")

        else:
            try:
                embedder = _build_dashscope(settings)
            except Exception as exc:  # noqa: BLE001 - 线上不可用要能降级
                if mode != "auto":
                    raise
                embedder = _build_hashing(
                    f"线上 Embedding 不可用（{str(exc)[:160]}），退回本地实现。\n"
                    f"            [注意] 本地向量只认字面重合，召回质量会明显下降；\n"
                    f"                   若索引原本是线上模型建的，必须重新执行 "
                    f"`python main.py index`，"
                    f"否则会拒绝检索（两个语义空间不能混用）。"
                )

    else:
        raise ValueError(
            f"未知的 NL2SQL_EMBEDDER={mode}，可选：auto / dashscope / hash"
        )

    _cache[mode] = embedder
    return embedder
