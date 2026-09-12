"""元数据中心（metadata.db）—— NL2SQL 的"数据知识本体"。

这里存的不是业务数据（订单、金额），而是"关于业务数据的知识"：

    meta_table         描述"业务表"       sales_order 是销售订单表
    meta_column        描述"业务字段"     pay_amount 是实际支付金额
    meta_relationship  描述"JOIN 关系"    sales_order_item.order_id -> sales_order.id
    meta_metric        描述"业务指标"     销售额 = SUM(pay_amount)
    meta_value         描述"字段取值"     region.region_name = 华南区
    retrieval_document 检索文档          给 Embedding 用的中间层（可重建）

关键设计：全部用 ID 互相引用，因为元数据中心自己也是完整的关系模型。
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import config


class MetadataBase(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# 1. meta_table：描述"业务表"
# ---------------------------------------------------------------------------
class MetaTable(MetadataBase):
    __tablename__ = "meta_table"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    datasource_name: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)

    # ---- 物理元数据：数据库自己就能告诉我们 ----
    physical_name: Mapped[str] = mapped_column(String(128), nullable=False)

    # ---- 语义元数据：必须人工/知识库补充 ----
    business_name: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    business_domain: Mapped[str | None] = mapped_column(String(128))
    grain: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("datasource_name", "schema_name", "physical_name"),
    )


# ---------------------------------------------------------------------------
# 2. meta_column：描述"业务字段"
# ---------------------------------------------------------------------------
class MetaColumn(MetadataBase):
    __tablename__ = "meta_column"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_id: Mapped[int] = mapped_column(
        ForeignKey("meta_table.id"), nullable=False
    )

    physical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    data_type: Mapped[str] = mapped_column(String(128))

    # identifier / foreign_key / dimension / measure / time ...
    semantic_role: Mapped[str | None] = mapped_column(String(64))
    is_primary_key: Mapped[bool] = mapped_column(Boolean, default=False)
    is_nullable: Mapped[bool] = mapped_column(Boolean, default=True)
    synonyms: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (
        UniqueConstraint("table_id", "physical_name"),
    )


# ---------------------------------------------------------------------------
# 3. meta_relationship：描述"JOIN 关系"
# ---------------------------------------------------------------------------
class MetaRelationship(MetadataBase):
    __tablename__ = "meta_relationship"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source_table_id: Mapped[int] = mapped_column(
        ForeignKey("meta_table.id"), nullable=False
    )
    source_column_id: Mapped[int] = mapped_column(
        ForeignKey("meta_column.id"), nullable=False
    )
    target_table_id: Mapped[int] = mapped_column(
        ForeignKey("meta_table.id"), nullable=False
    )
    target_column_id: Mapped[int] = mapped_column(
        ForeignKey("meta_column.id"), nullable=False
    )

    # foreign_key / inferred / manual ...
    relationship_type: Mapped[str] = mapped_column(String(32))
    # many_to_one / one_to_many / one_to_one
    cardinality: Mapped[str | None] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    description: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# 4. meta_metric：描述"业务指标"
# ---------------------------------------------------------------------------
class MetaMetric(MetadataBase):
    __tablename__ = "meta_metric"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_code: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )

    business_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    business_domain: Mapped[str | None] = mapped_column(String(128))

    # SUM / COUNT / AVG / ...
    aggregation: Mapped[str | None] = mapped_column(String(32))
    source_column_id: Mapped[int | None] = mapped_column(
        ForeignKey("meta_column.id")
    )
    time_column_id: Mapped[int | None] = mapped_column(
        ForeignKey("meta_column.id")
    )

    default_filters: Mapped[list] = mapped_column(JSON, default=list)
    synonyms: Mapped[list] = mapped_column(JSON, default=list)


# ---------------------------------------------------------------------------
# 5. meta_value：描述"字段取值"（Entity / Value Linking）
# ---------------------------------------------------------------------------
class MetaValue(MetadataBase):
    __tablename__ = "meta_value"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    column_id: Mapped[int] = mapped_column(
        ForeignKey("meta_column.id"), nullable=False
    )

    value: Mapped[str] = mapped_column(String(256), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(256))
    synonyms: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("column_id", "value"),
    )


# ---------------------------------------------------------------------------
# 6. retrieval_document：检索文档（结构化元数据 -> 适合 Embedding 的文本）
# ---------------------------------------------------------------------------
class RetrievalDocumentModel(MetadataBase):
    """为什么要落库？因为要有"增量索引"。

    业务描述改了 -> content 变了 -> content_hash 变了 -> 只需重算这一条的向量。
    """

    __tablename__ = "retrieval_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # "table:1" / "column:12" / "metric:1" / "value:3"
    # 不同实体的 id 可能重复，所以需要这个全局稳定主键
    entity_key: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_status: Mapped[str] = mapped_column(String(32), default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------
def get_metadata_engine():
    return create_engine(config.METADATA_DB_URL)


def init_metadata_db():
    engine = get_metadata_engine()
    MetadataBase.metadata.drop_all(engine)
    MetadataBase.metadata.create_all(engine)
    print(f"[metadata] 元数据中心已重建：{config.METADATA_DB_PATH}")
    return engine


if __name__ == "__main__":
    engine = init_metadata_db()
    with Session(engine) as s:
        pass
