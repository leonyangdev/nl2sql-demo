"""索引器：Metadata Repository -> Retrieval Document -> Embedding -> 向量库。

为什么要让 Retrieval Document 落库（retrieval_document 表）？

假设明天 pay_amount 的业务描述从

    实际支付金额

改成

    扣除优惠和退款后的净支付金额

你必须知道："这个元数据变了 -> 文档变了 -> 需要重新 Embedding -> 更新向量库"。
如果中间没有这一层，这件事就没法管理。

这就是 content_hash 的用途，也是所谓"增量索引"的基础。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from documents import (
    RetrievalDocument,
    build_all_retrieval_documents,
    content_hash,
)
from embedding import BaseEmbedder, get_embedder
from metadata_models import RetrievalDocumentModel, get_metadata_engine
from vector_store import BaseVectorStore, VectorRecord, get_vector_store


@dataclass
class IndexReport:
    total_documents: int = 0
    changed_keys: list[str] = field(default_factory=list)
    embedded_keys: list[str] = field(default_factory=list)
    skipped: bool = False


# ---------------------------------------------------------------------------
# 第一步：把 Retrieval Document 落库，并算出 content_hash
# ---------------------------------------------------------------------------
def persist_documents(
    session: Session,
    documents: list[RetrievalDocument],
) -> tuple[list[RetrievalDocument], list[RetrievalDocument]]:
    """返回 (内容变化的文档, 内容未变的文档)。"""
    changed: list[RetrievalDocument] = []
    unchanged: list[RetrievalDocument] = []

    for document in documents:
        digest = content_hash(document.content)

        row = session.scalar(
            select(RetrievalDocumentModel).where(
                RetrievalDocumentModel.entity_key == document.entity_key
            )
        )

        if row is None:
            session.add(
                RetrievalDocumentModel(
                    entity_type=document.entity_type,
                    entity_id=document.entity_id,
                    entity_key=document.entity_key,
                    content=document.content,
                    content_hash=digest,
                    embedding_status="pending",
                    version=1,
                )
            )
            changed.append(document)
        elif row.content_hash != digest:
            # 内容变了 -> 版本 +1 -> 标记待重新 Embedding
            row.content = document.content
            row.content_hash = digest
            row.embedding_status = "pending"
            row.version += 1
            changed.append(document)
        else:
            unchanged.append(document)

    session.commit()
    return changed, unchanged


def _mark_embedding_status(
    session: Session,
    entity_keys: list[str],
    model_name: str,
) -> None:
    """把指定的检索文档标记为"已 Embedding"。

    状态回写必须和"本轮要不要重建向量"解耦：向量库里已经有向量的文档，
    状态就该是 done —— 哪怕这一轮什么都不用做。
    """
    if not entity_keys:
        return

    rows = session.scalars(
        select(RetrievalDocumentModel).where(
            RetrievalDocumentModel.entity_key.in_(entity_keys)
        )
    ).all()
    for row in rows:
        row.embedding_model = model_name
        row.embedding_status = "done"


def _pending_entity_keys(session: Session) -> set[str]:
    """还没被标记为已 Embedding 的检索文档，用于兜底重算。"""
    return set(
        session.scalars(
            select(RetrievalDocumentModel.entity_key).where(
                RetrievalDocumentModel.embedding_status != "done"
            )
        ).all()
    )


def _corpus_signature(documents: list[RetrievalDocument]) -> str:
    joined = "|".join(
        f"{d.entity_key}:{content_hash(d.content)}" for d in documents
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 第二步：Embedding + 写入向量库
# ---------------------------------------------------------------------------
def build_index(
    force: bool = False,
    embedder: BaseEmbedder | None = None,
    store: BaseVectorStore | None = None,
) -> IndexReport:
    embedder = embedder or get_embedder()
    metadata_engine = get_metadata_engine()

    # ---- 1. 元数据 -> 检索文档 ----
    with Session(metadata_engine) as session:
        documents = build_all_retrieval_documents(session)
        changed, _ = persist_documents(session, documents)
        # persist 之后，所有"内容变了 / 刚被 init 整表重建"的文档都是 pending，
        # 它们才是本轮真正要处理的对象。
        # 不能只看 changed：上一轮若在回写状态前中断，content_hash 已经落库，
        # 这一轮它就不会再出现在 changed 里，于是永远卡在 pending。
        pending_keys = _pending_entity_keys(session)

    report = IndexReport(
        total_documents=len(documents),
        changed_keys=[d.entity_key for d in changed],
    )

    if not documents:
        print("[indexer] 元数据为空，请先执行 `python main.py init`")
        return report

    store = store or get_vector_store(dim=embedder.dim)

    signature = _corpus_signature(documents)
    stored_signature = store.get_meta("corpus_signature")
    stored_model = store.get_meta("embedding_model")

    # ---- 2. 判断是否可以跳过 ----
    up_to_date = (
        not force
        and stored_signature == signature
        and stored_model == embedder.name
        and store.count() == len(documents)
    )
    if up_to_date:
        report.skipped = True
        # 向量库已经是这批文档的最新状态，但 retrieval_document 可能刚被
        # `init` 整表重建（此时行行都是 pending）。状态必须在这里补写回来，
        # 否则"跳过重建"会留下"向量库有向量、落库却还是 pending"的矛盾状态。
        with Session(metadata_engine) as session:
            _mark_embedding_status(
                session, [d.entity_key for d in documents], embedder.name
            )
            session.commit()
        print(f"[indexer] 向量索引已是最新（{store.count()} 条），跳过重建")
        return report

    # 换了 Embedding 模型，或者向量条数对不上，必须整体重建，
    # 否则新旧向量不在同一个语义空间里。
    # 两种 embedder 都是"逐条独立计算"，所以只改了几条元数据时，
    # 真的只需要重算那几条 —— 增量索引在这里是成立的。
    needs_full_rebuild = (
        force
        or stored_model != embedder.name
        or store.count() != len(documents)
    )

    # 增量时按"当前仍是 pending 的文档"取，而不是按 changed：
    # 两种情况都覆盖得到，重复 Embedding 一次也无害（upsert 幂等）。
    to_embed = (
        documents
        if needs_full_rebuild
        else [d for d in documents if d.entity_key in pending_keys]
    )

    # ---- 3. Embedding ----
    vectors = embedder.encode([d.content for d in to_embed])

    records = [
        VectorRecord(
            entity_key=doc.entity_key,
            entity_type=doc.entity_type,
            entity_id=doc.entity_id,
            content=doc.content,
            embedding=vector,
            business_domain=doc.business_domain,
            table_id=doc.table_id,
        )
        for doc, vector in zip(to_embed, vectors)
    ]

    # ---- 4. 写入向量库 ----
    if needs_full_rebuild:
        store.reset()
    store.upsert(records)
    store.create_index(embedder.dim)
    store.set_meta("corpus_signature", signature)
    store.set_meta("embedding_model", embedder.name)

    report.embedded_keys = [r.entity_key for r in records]

    # ---- 5. 回写 Embedding 状态 ----
    with Session(metadata_engine) as session:
        _mark_embedding_status(
            session, [r.entity_key for r in records], embedder.name
        )
        session.commit()

    mode = "全量重建" if needs_full_rebuild else "增量更新"
    print(
        f"[indexer] {mode}完成：{len(documents)} 条文档 / "
        f"本次 Embedding {len(records)} 条（内容变化 {len(changed)} 条）"
    )
    return report


# ---------------------------------------------------------------------------
# 查看检索文档
# ---------------------------------------------------------------------------
def print_documents(limit: int | None = None) -> None:
    with Session(get_metadata_engine()) as session:
        rows = session.scalars(
            select(RetrievalDocumentModel).order_by(
                RetrievalDocumentModel.entity_type,
                RetrievalDocumentModel.id,
            )
        ).all()

        print(f"\n===== RETRIEVAL DOCUMENT（共 {len(rows)} 条）=====")
        for row in rows[: limit or len(rows)]:
            print(
                f"\n--- [{row.entity_key}] {row.entity_type} "
                f"v{row.version} / {row.embedding_status} "
                f"/ hash={row.content_hash[:8]} ---"
            )
            print(row.content)
