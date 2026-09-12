"""元数据采集器：把业务数据库"扫描"成元数据中心。

核心思想 —— 分两遍，先建 Node 再建 Edge：

    Pass 1  Table  -> Column      （建节点）
    Pass 2  Relationship          （建边）
    Pass 3  Metric / Value        （语义层）

为什么不能遍历一张表时一次搞定？
因为 sales_order_item 里有 product_id -> product.id，
但扫描到 sales_order_item 时 product 可能还没写入元数据中心，
target_table_id 根本拿不到。这和构建图数据完全一样。
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

import config
from business_config import BUSINESS_METADATA
from metadata_models import (
    MetaColumn,
    MetaMetric,
    MetaRelationship,
    MetaTable,
    MetaValue,
)

# SQLite / MySQL / PG 里能安全枚举的字符串类型
_ENUM_TYPES = ("CHAR", "TEXT", "VARCHAR", "STRING")


# ---------------------------------------------------------------------------
# Pass 1-A：采集 Table
# ---------------------------------------------------------------------------
def collect_tables(source_engine, session: Session) -> dict[str, MetaTable]:
    inspector = inspect(source_engine)
    table_mapping: dict[str, MetaTable] = {}

    for table_name in inspector.get_table_names():
        business_config = BUSINESS_METADATA["tables"].get(table_name, {})

        meta_table = MetaTable(
            datasource_name=config.DATASOURCE_NAME,
            schema_name=config.SCHEMA_NAME,
            physical_name=table_name,
            business_name=business_config.get("business_name"),
            description=business_config.get("description"),
            business_domain=business_config.get("business_domain"),
            grain=business_config.get("grain"),
        )
        session.add(meta_table)

        # 关键：马上 flush，才能拿到自增的 meta_table.id
        # 后面 Column 必须引用这个 id
        session.flush()

        table_mapping[table_name] = meta_table

    return table_mapping


# ---------------------------------------------------------------------------
# Pass 1-B：采集 Column
# ---------------------------------------------------------------------------
def collect_columns(
    source_engine,
    session: Session,
    table_mapping: dict[str, MetaTable],
) -> dict[tuple[str, str], MetaColumn]:
    inspector = inspect(source_engine)
    column_mapping: dict[tuple[str, str], MetaColumn] = {}

    for table_name, meta_table in table_mapping.items():
        columns = inspector.get_columns(table_name)
        primary_keys = set(
            inspector.get_pk_constraint(table_name).get("constrained_columns", [])
        )

        business_columns = (
            BUSINESS_METADATA["tables"].get(table_name, {}).get("columns", {})
        )

        for column in columns:
            column_name = column["name"]
            semantic_config = business_columns.get(column_name, {})

            meta_column = MetaColumn(
                table_id=meta_table.id,
                physical_name=column_name,
                business_name=semantic_config.get("business_name"),
                description=semantic_config.get("description"),
                data_type=str(column["type"]),
                semantic_role=semantic_config.get("semantic_role"),
                is_primary_key=column_name in primary_keys,
                is_nullable=bool(column["nullable"]),
                synonyms=semantic_config.get("synonyms", []),
            )
            session.add(meta_column)
            session.flush()

            column_mapping[(table_name, column_name)] = meta_column

    return column_mapping


# ---------------------------------------------------------------------------
# Pass 2：采集 Relationship（把数据库 FK 变成可消费的关系元数据）
# ---------------------------------------------------------------------------
def collect_relationships(
    source_engine,
    session: Session,
    table_mapping: dict[str, MetaTable],
    column_mapping: dict[tuple[str, str], MetaColumn],
) -> int:
    inspector = inspect(source_engine)
    count = 0

    for source_table_name in table_mapping:
        for fk in inspector.get_foreign_keys(source_table_name):
            target_table_name = fk["referred_table"]

            for source_column_name, target_column_name in zip(
                fk["constrained_columns"], fk["referred_columns"]
            ):
                # 约束名在 SQLite 里可能为 None，用列名兜底即可
                session.add(
                    MetaRelationship(
                        source_table_id=table_mapping[source_table_name].id,
                        source_column_id=column_mapping[
                            (source_table_name, source_column_name)
                        ].id,
                        target_table_id=table_mapping[target_table_name].id,
                        target_column_id=column_mapping[
                            (target_table_name, target_column_name)
                        ].id,
                        relationship_type="foreign_key",
                        cardinality="many_to_one",
                        source_type="database_fk",
                        confidence=1.0,
                        description=(
                            f"{source_table_name}.{source_column_name} "
                            f"-> {target_table_name}.{target_column_name}"
                        ),
                    )
                )
                count += 1

    return count


# ---------------------------------------------------------------------------
# Pass 3-A：采集 Metric（来自业务定义，不是数据库扫描）
# ---------------------------------------------------------------------------
def collect_metrics(
    session: Session,
    column_mapping: dict[tuple[str, str], MetaColumn],
) -> int:
    count = 0

    for metric_code, cfg in BUSINESS_METADATA["metrics"].items():
        source = cfg["source"]
        time_column_cfg = cfg.get("time_column")

        session.add(
            MetaMetric(
                metric_code=metric_code,
                business_name=cfg["business_name"],
                description=cfg.get("description"),
                business_domain=cfg.get("business_domain"),
                aggregation=cfg.get("aggregation"),
                source_column_id=column_mapping[
                    (source["table"], source["column"])
                ].id,
                time_column_id=(
                    column_mapping[
                        (time_column_cfg["table"], time_column_cfg["column"])
                    ].id
                    if time_column_cfg
                    else None
                ),
                default_filters=cfg.get("default_filters", []),
                synonyms=cfg.get("synonyms", []),
            )
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# Pass 3-B：采集 Value（低基数枚举 + 人工同义词）
# ---------------------------------------------------------------------------
def collect_values(
    source_session: Session,
    session: Session,
    column_mapping: dict[tuple[str, str], MetaColumn],
) -> int:
    """策略：
    低基数枚举 -> 全部建立 Value Metadata（region_name / status）
    高基数文本 -> 不进入元数据向量库（customer_name 300 万那种）

    注意：不能"看到 VARCHAR 就采样"。
    product.product_name 也是 VARCHAR，但它是实体名称而不是字典枚举，
    采样后会被当成过滤条件，生成 `product_name = '手机A' AND = '电脑B'`
    这种自相矛盾的 SQL。所以必须由业务配置显式声明 enumerable=True。
    """
    manual_values = BUSINESS_METADATA.get("values", {})
    business_tables = BUSINESS_METADATA["tables"]
    count = 0

    for (table_name, column_name), meta_column in column_mapping.items():
        data_type = (meta_column.data_type or "").upper()
        if not any(t in data_type for t in _ENUM_TYPES):
            continue

        column_config = (
            business_tables.get(table_name, {}).get("columns", {}).get(column_name, {})
        )
        if not column_config.get("enumerable"):
            continue

        rows = (
            source_session.execute(
                text(
                    f'SELECT DISTINCT "{column_name}" AS v '
                    f'FROM "{table_name}" '
                    f'WHERE "{column_name}" IS NOT NULL '
                    f"LIMIT {config.MAX_ENUM_VALUES + 1}"
                )
            )
            .scalars()
            .all()
        )

        # 超过上限 -> 判定为高基数，不采样
        if not rows or len(rows) > config.MAX_ENUM_VALUES:
            continue

        manual = manual_values.get(f"{table_name}.{column_name}", {})

        for raw_value in rows:
            value_str = str(raw_value)
            cfg = manual.get(value_str, {})

            session.add(
                MetaValue(
                    column_id=meta_column.id,
                    value=value_str,
                    business_name=cfg.get("business_name"),
                    synonyms=cfg.get("synonyms", []),
                    description=cfg.get("description"),
                )
            )
            count += 1

    return count


# ---------------------------------------------------------------------------
# 串联整个 Pipeline
# ---------------------------------------------------------------------------
def build_metadata(source_engine, metadata_engine) -> dict:
    """业务数据库 -> 自动扫描 -> 业务语义补充 -> 元数据落库。"""
    stats: dict[str, int] = {}

    with Session(metadata_engine) as session:
        table_mapping = collect_tables(source_engine, session)
        column_mapping = collect_columns(source_engine, session, table_mapping)
        stats["relationships"] = collect_relationships(
            source_engine, session, table_mapping, column_mapping
        )
        stats["metrics"] = collect_metrics(session, column_mapping)

        with Session(source_engine) as source_session:
            stats["values"] = collect_values(
                source_session, session, column_mapping
            )

        stats["tables"] = len(table_mapping)
        stats["columns"] = len(column_mapping)

        session.commit()

    print(
        "[collector] 采集完成："
        f"{stats['tables']} 表 / {stats['columns']} 字段 / "
        f"{stats['relationships']} 关系 / {stats['metrics']} 指标 / "
        f"{stats['values']} 取值"
    )
    return stats
