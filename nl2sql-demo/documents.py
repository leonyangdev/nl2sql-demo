"""Retrieval Document —— 结构化元数据 -> 适合语义检索的文本。

为什么不能直接把 `sales_order_item` 丢给 Embedding？
因为那只是数据库工程师看得懂的标识符。
Embedding 模型看到"销售订单明细 / 销售数量 / 实际支付金额 / 产品"，
以后才容易匹配"销售产品 / 成交金额 / 商品销售"。

所以：

    Metadata Entity  ->  Retrieval Document  ->  Embedding  ->  Vector

注意这里做的是"实体级"向量化（方案 C）：
Table、Column、Metric、Value 各自成为一条可检索实体，
靠 entity_type 区分，而不是"一张表一个向量"（长文本会淹没字段语义）。

Relationship 不在这里 —— 它解决的是"结构连接"，靠图遍历，不靠语义相似度。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from metadata_models import (
    MetaColumn,
    MetaMetric,
    MetaTable,
    MetaValue,
)


@dataclass
class RetrievalDocument:
    entity_type: str          # table / column / metric / value
    entity_id: int
    entity_key: str           # "table:1" / "column:12" / "metric:1" / "value:3"
    content: str              # 真正拿去 Embedding 的文本
    business_domain: str | None = None
    table_id: int | None = None   # 该实体归属于哪张表（Milvus 里用来做标量过滤）


def content_hash(text: str) -> str:
    """内容指纹，用于增量索引。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _join_synonyms(items) -> str:
    return "、".join(items or [])


# ---------------------------------------------------------------------------
# Table Document
# ---------------------------------------------------------------------------
def build_table_document(
    table: MetaTable,
    columns: list[MetaColumn],
) -> RetrievalDocument:
    column_text = []
    for column in columns:
        name = column.business_name or column.physical_name
        column_text.append(f"{column.physical_name}（{name}）")

    content = f"""
实体类型：数据表

物理表名：{table.physical_name}

业务名称：{table.business_name or ""}

业务域：{table.business_domain or ""}

业务描述：
{table.description or ""}

数据粒度：
{table.grain or ""}

主要字段：
{"、".join(column_text)}
""".strip()

    return RetrievalDocument(
        entity_type="table",
        entity_id=table.id,
        entity_key=f"table:{table.id}",
        content=content,
        business_domain=table.business_domain,
        table_id=table.id,
    )


# ---------------------------------------------------------------------------
# Column Document
# ---------------------------------------------------------------------------
def build_column_document(
    table: MetaTable,
    column: MetaColumn,
) -> RetrievalDocument:
    content = f"""
实体类型：字段

所属表：{table.physical_name}

所属业务表：{table.business_name or ""}

字段名：{column.physical_name}

业务名称：{column.business_name or ""}

业务含义：{column.description or ""}

语义角色：{column.semantic_role or ""}

常见叫法：{_join_synonyms(column.synonyms)}
""".strip()

    return RetrievalDocument(
        entity_type="column",
        entity_id=column.id,
        entity_key=f"column:{column.id}",
        content=content,
        business_domain=table.business_domain,
        table_id=table.id,
    )


# ---------------------------------------------------------------------------
# Metric Document（用户问数问题绝大部分围绕 Metric，所以文本最关键）
# ---------------------------------------------------------------------------
def build_metric_document(
    metric: MetaMetric,
    source_table: MetaTable,
    source_column: MetaColumn,
    time_table: MetaTable | None,
    time_column: MetaColumn | None,
) -> RetrievalDocument:
    default_filters = [
        f"{f['table']}.{f['column']} {f['operator']} {f['value']}"
        for f in (metric.default_filters or [])
    ]

    content = f"""
实体类型：业务指标

指标编码：{metric.metric_code}

指标名称：{metric.business_name}

业务域：{metric.business_domain or ""}

指标定义：{metric.description or ""}

计算方式：{metric.aggregation or ""}({source_table.physical_name}.{source_column.physical_name})

默认时间字段：{
        f"{time_table.physical_name}.{time_column.physical_name}"
        if time_table and time_column else ""
    }

默认过滤条件：{"；".join(default_filters)}

常见叫法：{_join_synonyms(metric.synonyms)}
""".strip()

    return RetrievalDocument(
        entity_type="metric",
        entity_id=metric.id,
        entity_key=f"metric:{metric.id}",
        content=content,
        business_domain=metric.business_domain,
    )


# ---------------------------------------------------------------------------
# Value Document（解决"华南" -> region.region_name = '华南区'）
# ---------------------------------------------------------------------------
def build_value_document(
    table: MetaTable,
    column: MetaColumn,
    value: MetaValue,
) -> RetrievalDocument:
    content = f"""
实体类型：字段值

所属表：{table.business_name or table.physical_name}

所属字段：{column.business_name or column.physical_name}

字段：{table.physical_name}.{column.physical_name}

标准值：{value.value}

业务名称：{value.business_name or value.value}

别名：{_join_synonyms(value.synonyms)}

说明：{value.description or ""}
""".strip()

    return RetrievalDocument(
        entity_type="value",
        entity_id=value.id,
        entity_key=f"value:{value.id}",
        content=content,
        business_domain=table.business_domain,
        table_id=table.id,
    )


# ---------------------------------------------------------------------------
# 把整个 Metadata Repository 转成 Retrieval Document
# ---------------------------------------------------------------------------
def build_all_retrieval_documents(session: Session) -> list[RetrievalDocument]:
    documents: list[RetrievalDocument] = []

    tables = session.scalars(select(MetaTable).order_by(MetaTable.id)).all()

    for table in tables:
        columns = session.scalars(
            select(MetaColumn)
            .where(MetaColumn.table_id == table.id)
            .order_by(MetaColumn.id)
        ).all()

        documents.append(build_table_document(table, columns))
        for column in columns:
            documents.append(build_column_document(table, column))

    # ---- Metric ----
    for metric in session.scalars(select(MetaMetric).order_by(MetaMetric.id)).all():
        source_column = session.get(MetaColumn, metric.source_column_id)
        source_table = (
            session.get(MetaTable, source_column.table_id) if source_column else None
        )

        time_column = session.get(MetaColumn, metric.time_column_id)
        time_table = (
            session.get(MetaTable, time_column.table_id) if time_column else None
        )

        if not source_column or not source_table:
            continue

        documents.append(
            build_metric_document(
                metric, source_table, source_column, time_table, time_column
            )
        )

    # ---- Value ----
    for value in session.scalars(select(MetaValue).order_by(MetaValue.id)).all():
        column = session.get(MetaColumn, value.column_id)
        if not column:
            continue
        table = session.get(MetaTable, column.table_id)
        if not table:
            continue
        documents.append(build_value_document(table, column, value))

    return documents
