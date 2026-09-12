"""向量库 —— "按语义找元数据"的检索索引。

两个后端，接口一致：

    LocalVectorStore   SQLite + 暴力检索（零依赖，默认，用于学习）
    MilvusVectorStore  真实 Milvus（docker + pymilvus）

再次强调职责边界：

    Metadata Repository = 元数据真相源（Source of Truth）
    Vector Store        = 语义检索索引（Search Index，随时可重建）

Milvus 里的 `entity_type / entity_id / business_domain / table_id` 只是
"向量记录的附加标签"（用于标量过滤），和我们说的 Metadata Repository
不是一个层次的东西，不要混淆。
"""

from __future__ import annotations

import math
import sqlite3
from abc import ABC, abstractmethod
from array import array
from dataclasses import dataclass, field

import config


@dataclass
class VectorRecord:
    entity_key: str
    entity_type: str
    entity_id: int
    content: str
    embedding: list[float]
    business_domain: str | None = None
    table_id: int | None = None


@dataclass
class SearchHit:
    entity_key: str
    entity_type: str
    entity_id: int
    content: str
    score: float
    business_domain: str | None = None
    table_id: int | None = None
    extra: dict = field(default_factory=dict)


class BaseVectorStore(ABC):
    @abstractmethod
    def reset(self) -> None:
        """清空集合（重建索引的第一步）。"""

    @abstractmethod
    def upsert(self, records: list[VectorRecord]) -> int:
        """按 entity_key 覆盖写入，保证幂等。"""

    @abstractmethod
    def search(
        self,
        vector: list[float],
        top_k: int = config.TOP_K,
        entity_types: list[str] | None = None,
        business_domain: str | None = None,
    ) -> list[SearchHit]:
        ...

    @abstractmethod
    def count(self) -> int:
        ...

    def iter_records(self) -> list[VectorRecord]:
        """列出已索引的向量记录（调试 / 巡检用）。"""
        return []

    def create_index(self, dim: int) -> None:
        """建向量索引。Local 后端不需要（暴力检索）。"""

    def get_meta(self, key: str) -> str | None:
        return None

    def set_meta(self, key: str, value: str) -> None:
        ...


# ---------------------------------------------------------------------------
# 本地实现：SQLite 存向量 + 全量暴力检索
# ---------------------------------------------------------------------------
class LocalVectorStore(BaseVectorStore):
    """所有向量装内存里逐个算点积。

    向量是 L2 归一化的，所以余弦相似度 == 点积。
    几十~几万条元数据完全够用，也顺便让你看清"向量检索"到底在算什么。
    （这就是 FAISS 里的 IndexFlat / Milvus 的 FLAT 索引）
    """

    def __init__(self, path=None):
        self.path = str(path or config.VECTOR_STORE_PATH)
        self._cache: list[tuple[dict, array]] | None = None
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_record (
                    entity_key      TEXT PRIMARY KEY,
                    entity_type     TEXT NOT NULL,
                    entity_id       INTEGER NOT NULL,
                    business_domain TEXT,
                    table_id        INTEGER,
                    content         TEXT NOT NULL,
                    dim             INTEGER NOT NULL,
                    embedding       BLOB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def reset(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM vector_record")
        self._cache = None

    def upsert(self, records: list[VectorRecord]) -> int:
        rows = [
            (
                r.entity_key,
                r.entity_type,
                r.entity_id,
                r.business_domain or "",
                r.table_id if r.table_id is not None else -1,
                r.content,
                len(r.embedding),
                array("f", r.embedding).tobytes(),
            )
            for r in records
        ]

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO vector_record (
                    entity_key, entity_type, entity_id, business_domain,
                    table_id, content, dim, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_key) DO UPDATE SET
                    entity_type     = excluded.entity_type,
                    entity_id       = excluded.entity_id,
                    business_domain = excluded.business_domain,
                    table_id        = excluded.table_id,
                    content         = excluded.content,
                    dim             = excluded.dim,
                    embedding       = excluded.embedding
                """,
                rows,
            )

        self._cache = None
        return len(rows)

    def _load(self) -> list[tuple[dict, array]]:
        if self._cache is not None:
            return self._cache

        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT entity_key, entity_type, entity_id,
                       business_domain, table_id, content, dim, embedding
                FROM vector_record
                """
            )
            raw_rows = cursor.fetchall()

        loaded: list[tuple[dict, array]] = []
        for (
            entity_key, entity_type, entity_id,
            business_domain, table_id, content, dim, blob,
        ) in raw_rows:
            vector = array("f")
            vector.frombytes(blob)
            if len(vector) != dim:
                continue
            loaded.append(
                (
                    {
                        "entity_key": entity_key,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "business_domain": business_domain or None,
                        "table_id": None if table_id == -1 else table_id,
                        "content": content,
                    },
                    vector,
                )
            )

        self._cache = loaded
        return loaded

    def search(
        self,
        vector: list[float],
        top_k: int = config.TOP_K,
        entity_types: list[str] | None = None,
        business_domain: str | None = None,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []

        for meta, stored in self._load():
            if entity_types and meta["entity_type"] not in entity_types:
                continue
            if business_domain and meta["business_domain"] != business_domain:
                continue

            # 余弦相似度（向量已归一化，点积即可）
            score = math.fsum(a * b for a, b in zip(vector, stored))
            hits.append(SearchHit(score=score, **meta))

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM vector_record").fetchone()[0])

    def iter_records(self) -> list[VectorRecord]:
        records: list[VectorRecord] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entity_key, entity_type, entity_id, business_domain,
                       table_id, content, embedding
                FROM vector_record
                ORDER BY entity_type, entity_id
                """
            ).fetchall()

        for (
            entity_key, entity_type, entity_id,
            business_domain, table_id, content, blob,
        ) in rows:
            vector = array("f")
            vector.frombytes(blob)
            records.append(
                VectorRecord(
                    entity_key=entity_key,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    content=content,
                    embedding=list(vector),
                    business_domain=business_domain or None,
                    table_id=None if table_id == -1 else table_id,
                )
            )
        return records

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM vector_meta WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO vector_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


# ---------------------------------------------------------------------------
# Milvus 实现（可选）
# ---------------------------------------------------------------------------
class MilvusVectorStore(BaseVectorStore):
    """对应课程里讲的：一张 Collection + 标量字段做过滤 + HNSW 索引。

    启动 Milvus 之后：
        pip install pymilvus
        set NL2SQL_VECTOR_STORE=milvus
    """

    def __init__(self, collection_name: str = config.COLLECTION_NAME, dim: int = 0):
        from pymilvus import (  # 延迟导入
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )

        self._DataType = DataType
        self._FieldSchema = FieldSchema
        self._CollectionSchema = CollectionSchema
        self._utility = utility

        connections.connect(
            alias="default", host=config.MILVUS_HOST, port=config.MILVUS_PORT
        )

        self.collection_name = collection_name
        self.dim = dim or config.HASH_EMBEDDING_DIM

        if utility.has_collection(collection_name):
            self.collection = Collection(collection_name)
            self.collection.load()
        else:
            self.collection = Collection(
                name=collection_name,
                schema=CollectionSchema(
                    fields=self._build_fields(dim),
                    description="NL2SQL metadata semantic index",
                ),
            )

    def _build_fields(self, dim: int) -> list:
        DataType, FieldSchema = self._DataType, self._FieldSchema

        return [
            FieldSchema("entity_key", DataType.VARCHAR, is_primary=True, max_length=128),
            FieldSchema("entity_type", DataType.VARCHAR, max_length=32),
            FieldSchema("entity_id", DataType.INT64),
            FieldSchema("business_domain", DataType.VARCHAR, max_length=128),
            FieldSchema("table_id", DataType.INT64),
            FieldSchema("content", DataType.VARCHAR, max_length=8192),
            FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=dim),
        ]

    def reset(self) -> None:
        if self._utility.has_collection(self.collection_name):
            self._utility.drop_collection(self.collection_name)

    def upsert(self, records: list[VectorRecord]) -> int:
        rows = [
            [
                r.entity_key,
                r.entity_type,
                r.entity_id,
                r.business_domain or "",
                r.table_id if r.table_id is not None else -1,
                r.content[:8000],
                r.embedding,
            ]
            for r in records
        ]
        self.collection.upsert(rows)
        self.collection.flush()
        return len(rows)

    def search(
        self,
        vector: list[float],
        top_k: int = config.TOP_K,
        entity_types: list[str] | None = None,
        business_domain: str | None = None,
    ) -> list[SearchHit]:
        expressions = []
        if entity_types:
            joined = ", ".join(f'"{t}"' for t in entity_types)
            expressions.append(f"entity_type in [{joined}]")
        if business_domain:
            expressions.append(f'business_domain == "{business_domain}"')
        expr = " and ".join(expressions) or None

        results = self.collection.search(
            data=[vector],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=top_k,
            expr=expr,
            output_fields=[
                "entity_key", "entity_type", "entity_id",
                "business_domain", "table_id", "content",
            ],
        )

        hits: list[SearchHit] = []
        for hit in results[0]:
            entity = hit.entity
            hits.append(
                SearchHit(
                    entity_key=entity.get("entity_key"),
                    entity_type=entity.get("entity_type"),
                    entity_id=entity.get("entity_id"),
                    content=entity.get("content"),
                    score=float(hit.score),
                    business_domain=entity.get("business_domain") or None,
                    table_id=(
                        None if entity.get("table_id") == -1
                        else entity.get("table_id")
                    ),
                )
            )
        return hits

    def count(self) -> int:
        return int(self.collection.num_entities)

    def iter_records(self) -> list[VectorRecord]:
        rows = self.collection.query(
            expr="entity_id >= 0",
            output_fields=[
                "entity_key", "entity_type", "entity_id",
                "business_domain", "table_id", "content",
            ],
            limit=16384,
        )
        return [
            VectorRecord(
                entity_key=row["entity_key"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                content=row["content"],
                embedding=[],
                business_domain=row["business_domain"] or None,
                table_id=None if row["table_id"] == -1 else row["table_id"],
            )
            for row in rows
        ]

    def create_index(self, dim: int) -> None:
        self.collection.create_index(
            field_name="embedding",
            index_params={
                "metric_type": "COSINE",
                "index_type": "HNSW",
                "params": {"M": 16, "efConstruction": 200},
            },
        )
        self.collection.load()
        print("[vector] 已创建 HNSW 向量索引")


def get_vector_store(dim: int = 0, backend: str | None = None) -> BaseVectorStore:
    backend = (backend or config.VECTOR_STORE_BACKEND).lower()

    if backend == "milvus":
        store = MilvusVectorStore(dim=dim)
        print(f"[vector] 使用 Milvus：{config.MILVUS_HOST}:{config.MILVUS_PORT}")
        return store

    store = LocalVectorStore()
    print(f"[vector] 使用本地向量库（SQLite + 暴力检索）：{config.VECTOR_STORE_PATH}")
    return store
