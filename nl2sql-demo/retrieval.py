"""在线阶段（第二阶段）：用户问题 -> 检索 -> JOIN 图 -> SQL。

这一节把整条链路真正闭合：

    用户问题
      ↓ ① 同义词扩展（Metadata 里的 synonyms 反哺 Query）
      ↓ ② Embedding -> Vector Store 语义召回（找到"谁"）
      ↓ ③ 回 Metadata Repository 取完整定义（知道"它是什么"）
      ↓ ④ JOIN 图遍历补齐缺失的表（Relationship 不靠向量，靠图）
      ↓ ⑤ 组装 SQL -> 在业务数据库上执行

V1 的 SQL 生成是"规则 + 槽位填充"，不是让大模型自由发挥。
先看清每个槽位从哪来，以后换成 LLM 生成 SQL 时，喂给它的上下文就是这里的东西。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.orm import Session

import config
from embedding import BaseEmbedder, get_embedder
from metadata_models import (
    MetaColumn,
    MetaMetric,
    MetaRelationship,
    MetaTable,
    MetaValue,
    get_metadata_engine,
)
from vector_store import BaseVectorStore, SearchHit, get_vector_store


# ===========================================================================
# 数据结构
# ===========================================================================
@dataclass
class MatchedEntity:
    entity_key: str
    entity_type: str
    score: float
    label: str
    payload: dict = field(default_factory=dict)


@dataclass
class RetrievalContext:
    question: str
    expanded_question: str = ""
    matched_aliases: list[tuple[str, str, str]] = field(default_factory=list)
    entities: dict[str, list[MatchedEntity]] = field(default_factory=dict)
    join_steps: list[dict] = field(default_factory=list)
    join_tables: set[str] = field(default_factory=set)
    join_graph: "JoinGraph | None" = None
    notes: list[str] = field(default_factory=list)
    sql: str | None = None


@dataclass
class JoinEdge:
    left_table: str
    left_column: str
    right_table: str
    right_column: str

    def key(self) -> tuple:
        return tuple(
            sorted(
                [
                    (self.left_table, self.left_column),
                    (self.right_table, self.right_column),
                ]
            )
        )


# ===========================================================================
# ① 同义词扩展：Metadata 里的 synonyms 反过来帮助 Query 命中
# ===========================================================================
def build_alias_index(session: Session) -> list[tuple[str, str, str]]:
    """(别名, 标准名, 实体类型) —— 用户口语 -> Metadata 里的标准业务名。"""
    index: list[tuple[str, str, str]] = []

    for metric in session.scalars(select(MetaMetric)).all():
        for alias in metric.synonyms or []:
            index.append((alias, metric.business_name, "metric"))

    for column in session.scalars(select(MetaColumn)).all():
        if not column.business_name:
            continue
        for alias in column.synonyms or []:
            index.append((alias, column.business_name, "column"))

    for value in session.scalars(select(MetaValue)).all():
        for alias in value.synonyms or []:
            index.append((alias, value.value, "value"))

    return index


def expand_question(
    question: str,
    alias_index: list[tuple[str, str, str]],
) -> tuple[str, list[tuple[str, str, str]]]:
    """把用户口语里的同义词，替换/补充成 Metadata 的标准叫法。

    "成交额最高的商品" -> "成交额最高的商品 销售额 产品名称"

    这是很实用的一招：纯字面向量检索抓不到"成交额 ≈ 销售额"，
    但元数据里早就写了这层同义词关系。

    实现上按"长别名优先 + 位置占用"来做，避免 "成交金额" 被 "成交" 抢走。
    局限：仍然是朴素子串匹配，不是真正的实体识别（生产环境要换成 NER）。
    """
    ordered = sorted(alias_index, key=lambda item: len(item[0]), reverse=True)

    consumed = [False] * len(question)
    matched: list[tuple[str, str, str]] = []
    extras: list[str] = []

    for alias, canonical, entity_type in ordered:
        if len(alias) < 2 or alias == canonical:
            continue

        start = 0
        while True:
            index = question.find(alias, start)
            if index < 0:
                break

            span = range(index, index + len(alias))
            if not any(consumed[i] for i in span):
                for i in span:
                    consumed[i] = True
                matched.append((alias, canonical, entity_type))
                if canonical not in question:
                    extras.append(canonical)

            start = index + 1

    expanded = f"{question} {' '.join(dict.fromkeys(extras))}" if extras else question
    return expanded, matched


# ===========================================================================
# ② 语义召回
# ===========================================================================
def ensure_embedding_space(
    store: BaseVectorStore,
    embedder: BaseEmbedder,
) -> None:
    """向量库里的向量必须和当前 embedder 在同一语义空间，否则检索毫无意义。

    最容易踩到的场景：索引是用线上模型建的，之后线上接口临时挂了，
    `auto` 模式降级成本地哈希向量，这时如果只跑 `ask` 不重建索引，
    就是"用哈希向量去查线上向量库" —— 点积结果纯属随机，却仍然会返回
    Top-K 结果，悄无声息地给出错误答案。

    宁可在这里直接报错，也不要返回看起来正常但实际错误的结果。
    """
    stored = store.get_meta("embedding_model")
    if stored and stored != embedder.name:
        raise RuntimeError(
            f"向量索引是用 [{stored}] 生成的，当前使用的是 [{embedder.name}]。\n"
            f"  两者不在同一个语义空间，相似度计算结果无意义，因此拒绝检索。\n"
            f"  解决办法（二选一）：\n"
            f"    1) python main.py index              # 用当前模型重建索引\n"
            f"    2) 让 NL2SQL_EMBEDDER 与索引保持一致"
        )


def semantic_search(
    store: BaseVectorStore,
    embedder: BaseEmbedder,
    question: str,
    top_k: int = config.TOP_K,
) -> list[SearchHit]:
    vector = embedder.encode([question])[0]
    return store.search(vector, top_k=top_k)


# ===========================================================================
# ③ 回 Metadata Repository 取完整定义
# ===========================================================================
def hydrate(session: Session, hits: list[SearchHit]) -> dict[str, list[MatchedEntity]]:
    result: dict[str, list[MatchedEntity]] = {
        "metric": [], "column": [], "table": [], "value": []
    }

    for hit in hits:
        if hit.score < config.MIN_SCORE:
            continue

        entity_type = hit.entity_type
        payload: dict | None = None
        label = hit.entity_key

        if entity_type == "metric":
            metric = session.get(MetaMetric, hit.entity_id)
            if metric:
                source_column = session.get(MetaColumn, metric.source_column_id)
                source_table = (
                    session.get(MetaTable, source_column.table_id)
                    if source_column
                    else None
                )
                time_column = session.get(MetaColumn, metric.time_column_id)
                time_table = (
                    session.get(MetaTable, time_column.table_id)
                    if time_column
                    else None
                )
                if source_column and source_table:
                    payload = {
                        "code": metric.metric_code,
                        "business_name": metric.business_name,
                        "aggregation": metric.aggregation,
                        "synonyms": metric.synonyms or [],
                        "source_table": source_table.physical_name,
                        "source_column": source_column.physical_name,
                        "time_table": time_table.physical_name if time_table else None,
                        "time_column": (
                            time_column.physical_name if time_column else None
                        ),
                        "default_filters": metric.default_filters or [],
                    }
                    label = f"指标 {metric.business_name}（{metric.metric_code}）"

        elif entity_type == "column":
            column = session.get(MetaColumn, hit.entity_id)
            table = session.get(MetaTable, column.table_id) if column else None
            if column and table:
                payload = {
                    "physical_name": column.physical_name,
                    "business_name": column.business_name,
                    "semantic_role": column.semantic_role,
                    "table": table.physical_name,
                    "table_business_name": table.business_name,
                }
                label = (
                    f"字段 {table.physical_name}.{column.physical_name}"
                    f"（{column.business_name}）"
                )

        elif entity_type == "table":
            table = session.get(MetaTable, hit.entity_id)
            if table:
                columns = session.scalars(
                    select(MetaColumn).where(MetaColumn.table_id == table.id)
                ).all()
                payload = {
                    "physical_name": table.physical_name,
                    "business_name": table.business_name,
                    "columns": [
                        {
                            "physical_name": c.physical_name,
                            "business_name": c.business_name,
                            "semantic_role": c.semantic_role,
                        }
                        for c in columns
                    ],
                }
                label = f"数据表 {table.physical_name}（{table.business_name}）"

        elif entity_type == "value":
            value = session.get(MetaValue, hit.entity_id)
            column = session.get(MetaColumn, value.column_id) if value else None
            table = session.get(MetaTable, column.table_id) if column else None
            if value and column and table:
                payload = {
                    "value": value.value,
                    "synonyms": value.synonyms or [],
                    "column": column.physical_name,
                    "table": table.physical_name,
                    "column_business_name": column.business_name,
                }
                label = (
                    f"取值 {table.physical_name}.{column.physical_name}"
                    f" = {value.value}"
                )

        if payload is not None:
            result[entity_type].append(
                MatchedEntity(
                    entity_key=hit.entity_key,
                    entity_type=entity_type,
                    score=hit.score,
                    label=label,
                    payload=payload,
                )
            )

    for entity_type, items in result.items():
        items.sort(key=lambda e: e.score, reverse=True)
        result[entity_type] = items[: config.PER_TYPE_TOP_K]

    return result


# ===========================================================================
# ④ JOIN 图：Relationship 不靠向量，靠图遍历
# ===========================================================================
class JoinGraph:
    def __init__(self) -> None:
        self._adjacency: dict[str, list[tuple[str, JoinEdge]]] = {}

    @classmethod
    def from_metadata(cls, session: Session) -> "JoinGraph":
        graph = cls()
        table_names = {
            t.id: t.physical_name for t in session.scalars(select(MetaTable)).all()
        }
        column_names = {
            c.id: c.physical_name for c in session.scalars(select(MetaColumn)).all()
        }

        for rel in session.scalars(select(MetaRelationship)).all():
            edge = JoinEdge(
                left_table=table_names[rel.source_table_id],
                left_column=column_names[rel.source_column_id],
                right_table=table_names[rel.target_table_id],
                right_column=column_names[rel.target_column_id],
            )
            graph._add(edge.left_table, edge.right_table, edge)
            graph._add(edge.right_table, edge.left_table, edge)

        return graph

    def _add(self, node: str, neighbor: str, edge: JoinEdge) -> None:
        self._adjacency.setdefault(node, []).append((neighbor, edge))

    def all_edges(self) -> list[JoinEdge]:
        """整张 JOIN 图（去重）—— 交给大模型作为"允许的连接方式"。"""
        seen: dict[tuple, JoinEdge] = {}
        for neighbors in self._adjacency.values():
            for _neighbor, edge in neighbors:
                seen.setdefault(edge.key(), edge)
        return list(seen.values())

    def shortest_path(self, source: str, target: str) -> list[JoinEdge] | None:
        """BFS 找最短 JOIN 路径 —— 和"最少 JOIN 几张表"是一个意思。"""
        if source == target:
            return []

        visited = {source}
        queue = deque([source])
        previous: dict[str, tuple[str, JoinEdge]] = {}

        while queue:
            node = queue.popleft()
            for neighbor, edge in self._adjacency.get(node, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                previous[neighbor] = (node, edge)

                if neighbor == target:
                    path: list[JoinEdge] = []
                    cursor = target
                    while cursor in previous:
                        parent, used_edge = previous[cursor]
                        path.append(used_edge)
                        cursor = parent
                    path.reverse()
                    return path

                queue.append(neighbor)

        return None

    def plan(self, root: str, targets: list[str]) -> tuple[list[dict], set[str]]:
        """从 root 出发，把 targets 全部接进查询里，返回有序的 JOIN 步骤。"""
        used = {root}
        steps: list[dict] = []
        pending = [t for t in dict.fromkeys(targets) if t and t != root]

        while pending:
            progress = False
            for target in list(pending):
                best: list[JoinEdge] | None = None
                for anchor in list(used):
                    path = self.shortest_path(anchor, target)
                    if path is not None and (best is None or len(path) < len(best)):
                        best = path

                if best is None:
                    continue

                for edge in best:
                    if edge.left_table in used and edge.right_table not in used:
                        new_table, new_column = edge.right_table, edge.right_column
                        ref_table, ref_column = edge.left_table, edge.left_column
                    elif edge.right_table in used and edge.left_table not in used:
                        new_table, new_column = edge.left_table, edge.left_column
                        ref_table, ref_column = edge.right_table, edge.right_column
                    else:
                        continue

                    used.add(new_table)
                    steps.append(
                        {
                            "table": new_table,
                            "column": new_column,
                            "ref_table": ref_table,
                            "ref_column": ref_column,
                        }
                    )

                pending.remove(target)
                progress = True

            if not progress:
                break

        return steps, used


# ===========================================================================
# ⑤ SQL 生成（规则 + 槽位填充）
# ===========================================================================
_MONTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
_MONTH_RE_ALT = re.compile(r"(\d{4})\s*-\s*(\d{1,2})(?!\d)")
_YEAR_RE = re.compile(r"(\d{4})\s*年(?:\s*度)?")
_RELATIVE_RE = re.compile(r"(今年|去年|明年|本月|这个月|上月|上个月)")
_LIMIT_RE = re.compile(r"(?:前|top|Top|TOP)\s*(\d+)|(\d+)\s*(?:个|条|名|款)")
_DESC_WORDS = ("最高", "最大", "最多", "最好", "降序", "从高到低")
_ASC_WORDS = ("最低", "最小", "最少", "最差", "升序", "从低到高")

# 出现这些词，才认为用户想"按某个维度分组看"
_GROUP_WORDS = (
    "哪个", "哪些", "什么", "各", "每", "分别", "排名", "排行",
    "top", "Top", "TOP", "前", "对比", "比较",
    "最高", "最低", "最好", "最差", "最大", "最小", "最多", "最少",
)


def _month_range(year: int, month: int) -> tuple[str, str]:
    return (
        date(year, month, 1).isoformat(),
        date(year + (month == 12), (month % 12) + 1, 1).isoformat(),
    )


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def parse_time_range(question: str) -> tuple[str, str] | None:
    """把"2026 年 8 月"变成半开区间 [2026-08-01, 2026-09-01)。

    统一用左闭右开区间，天然避开"8 月最后一天是 30 还是 31 号"这类经典坑。
    """
    match = _MONTH_RE.search(question) or _MONTH_RE_ALT.search(question)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return _month_range(year, month)

    match = _YEAR_RE.search(question)
    if match:
        year = int(match.group(1))
        return date(year, 1, 1).isoformat(), date(year + 1, 1, 1).isoformat()

    # 相对时间（V1 只支持最常见的几个）
    match = _RELATIVE_RE.search(question)
    if match:
        today = date.today()
        token = match.group(1)

        if token in ("今年", "去年", "明年"):
            year = today.year + {"今年": 0, "去年": -1, "明年": 1}[token]
            return date(year, 1, 1).isoformat(), date(year + 1, 1, 1).isoformat()

        if token in ("本月", "这个月"):
            return _month_range(today.year, today.month)

        if token in ("上月", "上个月"):
            return _month_range(*_shift_month(today.year, today.month, -1))

    return None


def needs_group_by(question: str) -> bool:
    """判断用户到底想不想要"分组"。

    "去年华南区的销售额"  -> 一个数，不该 GROUP BY
    "哪个区域卖得最好"    -> 要按区域分组

    不能"只要能找到 dimension 字段就 GROUP BY"，否则会生成
    `GROUP BY sales_order.status` 这种用户根本没要的结果。
    """
    return any(word in question for word in _GROUP_WORDS)


def parse_limit(question: str) -> int | None:
    match = _LIMIT_RE.search(question)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def parse_direction(question: str) -> str | None:
    if any(word in question for word in _DESC_WORDS):
        return "DESC"
    if any(word in question for word in _ASC_WORDS):
        return "ASC"
    return None


def _literal(value) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def select_value_filters(
    entities: dict[str, list[MatchedEntity]],
    question: str,
    matched_aliases: list[tuple[str, str, str]],
) -> list[MatchedEntity]:
    """决定哪些 Value 真的能变成 WHERE 条件。

    关键：不能"向量相似度高就用"。

    用户问 "今年哪个区域卖得最好" 时，区域名称的取值（华南区/华东区/华北区）
    向量得分都不低，但用户根本没指定哪个区域。
    如果照搬，就会生成 `region_name = '华北区'` —— 语义上完全错。

    正确做法：只有"用户确实提到了这个值（原文或别名）"时才建立映射。
    Value Metadata 的职责是 Value Linking，不是相似度召回。

    同时同一个字段只保留得分最高的取值，否则会拼出
    `product_name = 'A' AND product_name = 'B'` 这种永远为空的 SQL。
    """
    mentioned = {
        canonical for _alias, canonical, entity_type in matched_aliases
        if entity_type == "value"
    }

    best: dict[tuple[str, str], MatchedEntity] = {}

    for entity in entities.get("value", []):
        payload = entity.payload
        tokens = [payload["value"], *(payload.get("synonyms") or [])]

        hit = payload["value"] in mentioned or any(
            token and token in question for token in tokens
        )
        if not hit:
            continue

        key = (payload["table"], payload["column"])
        if key not in best:
            best[key] = entity

    return list(best.values())


def _pick_dimension(
    entities: dict[str, list[MatchedEntity]],
    metric_payload: dict,
    filter_columns: set[tuple[str, str]],
) -> MatchedEntity | None:
    """选 GROUP BY 维度：优先直接命中的 dimension 字段，其次从命中的表里兜底。"""
    excluded = {
        (metric_payload["source_table"], metric_payload["source_column"]),
        (
            metric_payload.get("time_table"),
            metric_payload.get("time_column"),
        ),
    } | filter_columns

    for candidate in entities.get("column", []):
        payload = candidate.payload
        if payload.get("semantic_role") != "dimension":
            continue
        if (payload["table"], payload["physical_name"]) in excluded:
            continue
        return candidate

    for table_entity in entities.get("table", []):
        for column in table_entity.payload.get("columns", []):
            if column["semantic_role"] != "dimension":
                continue
            if (table_entity.payload["physical_name"], column["physical_name"]) in excluded:
                continue
            return MatchedEntity(
                entity_key=f"column@{table_entity.payload['physical_name']}.{column['physical_name']}",
                entity_type="column",
                score=table_entity.score,
                label=(
                    f"字段 {table_entity.payload['physical_name']}."
                    f"{column['physical_name']}（{column['business_name']}）"
                ),
                payload={
                    "physical_name": column["physical_name"],
                    "business_name": column["business_name"],
                    "semantic_role": column["semantic_role"],
                    "table": table_entity.payload["physical_name"],
                    "table_business_name": table_entity.payload["business_name"],
                },
            )

    return None


def plan_sql(context: RetrievalContext) -> str | None:
    entities = context.entities
    metrics = entities.get("metric", [])
    if not metrics:
        context.notes.append("没有召回任何 Metric，无法确定聚合口径（V1 直接放弃）")
        return None

    metric = metrics[0].payload
    base_table = metric["source_table"]
    expression = (
        f"{metric['aggregation']}({base_table}.{metric['source_column']})"
    )

    select_parts: list[str] = []
    group_by: list[str] = []
    required_tables = {base_table}

    # ---- 先定下取值过滤，避免"过滤字段"又被选成 GROUP BY 维度 ----
    value_filters = select_value_filters(
        entities, context.question, context.matched_aliases
    )
    filter_columns = {
        (v.payload["table"], v.payload["column"]) for v in value_filters
    }

    # ---- 维度（GROUP BY）----
    group_intent = needs_group_by(context.question)
    dimension = (
        _pick_dimension(entities, metric, filter_columns) if group_intent else None
    )
    if not group_intent:
        context.notes.append("问题中没有分组意图，只做整体聚合")
    if dimension:
        payload = dimension.payload
        qualified = f"{payload['table']}.{payload['physical_name']}"
        select_parts.append(f"{qualified} AS {payload['physical_name']}")
        group_by.append(qualified)
        required_tables.add(payload["table"])
        context.notes.append(f"GROUP BY 维度取自：{dimension.label}")

    select_parts.append(f"{expression} AS {metric['code']}")

    where_parts: list[str] = []

    # ---- Metric 默认过滤条件（业务口径，必须带上）----
    for rule in metric["default_filters"]:
        qualified = f"{rule['table']}.{rule['column']}"
        where_parts.append(
            f"{qualified} {rule.get('operator', '=')} {_literal(rule['value'])}"
        )
        required_tables.add(rule["table"])

    # ---- 时间范围：用 Metric 定义的"默认时间字段" ----
    if metric.get("time_table") and metric.get("time_column"):
        time_range = parse_time_range(context.question)
        if time_range:
            qualified = f"{metric['time_table']}.{metric['time_column']}"
            where_parts.append(f"{qualified} >= {_literal(time_range[0])}")
            where_parts.append(f"{qualified} < {_literal(time_range[1])}")
            required_tables.add(metric["time_table"])
            context.notes.append(
                f"时间范围 {time_range[0]} ~ {time_range[1]} 落在 {qualified}"
            )

    # ---- Value 过滤（华东区 / 华南区 ……）----
    for value_entity in value_filters:
        payload = value_entity.payload
        qualified = f"{payload['table']}.{payload['column']}"
        where_parts.append(f"{qualified} = {_literal(payload['value'])}")
        required_tables.add(payload["table"])
        context.notes.append(f"取值过滤取自：{value_entity.label}")

    # ---- 用 JOIN 图补齐缺失的表 ----
    graph = context.join_graph or JoinGraph()
    steps, used_tables = graph.plan(base_table, sorted(required_tables))
    context.join_steps = steps
    context.join_tables = used_tables

    for step in steps:
        context.notes.append(
            f"JOIN 补齐：{step['table']} "
            f"ON {step['table']}.{step['column']} = "
            f"{step['ref_table']}.{step['ref_column']}"
        )

    # ---- 组装 SQL ----
    # 去重：同一条过滤条件可能同时来自 Metric 默认过滤和 Value 映射
    where_parts = list(dict.fromkeys(where_parts))

    lines = ["SELECT", "  " + ",\n  ".join(select_parts), f"FROM {base_table}"]
    for step in steps:
        lines.append(
            f"JOIN {step['table']} "
            f"ON {step['table']}.{step['column']} = "
            f"{step['ref_table']}.{step['ref_column']}"
        )

    if where_parts:
        lines.append("WHERE " + "\n  AND ".join(where_parts))

    if group_by:
        lines.append("GROUP BY " + ", ".join(group_by))

        direction = parse_direction(context.question) or "DESC"
        lines.append(f"ORDER BY {metric['code']} {direction}")

        limit = parse_limit(context.question)
        if limit:
            lines.append(f"LIMIT {limit}")
            context.notes.append(f"排序 {direction} + LIMIT {limit}")

    return "\n".join(lines)


# ===========================================================================
# 完整在线流程
# ===========================================================================
def retrieve(
    question: str,
    embedder: BaseEmbedder | None = None,
    store: BaseVectorStore | None = None,
) -> RetrievalContext:
    embedder = embedder or get_embedder()
    store = store or get_vector_store(dim=embedder.dim)

    ensure_embedding_space(store, embedder)

    context = RetrievalContext(question=question)

    with Session(get_metadata_engine()) as session:
        alias_index = build_alias_index(session)
        context.expanded_question, context.matched_aliases = expand_question(
            question, alias_index
        )

    hits = semantic_search(store, embedder, context.expanded_question)

    with Session(get_metadata_engine()) as session:
        context.entities = hydrate(session, hits)
        context.join_graph = JoinGraph.from_metadata(session)

    context.sql = plan_sql(context)
    return context


def print_trace(context: RetrievalContext) -> None:
    print("\n===== ① 问题理解 =====")
    print(f"原始问题：{context.question}")
    print(f"扩展后  ：{context.expanded_question}")
    for alias, canonical, entity_type in context.matched_aliases:
        print(f"  同义词命中：{alias} -> {canonical}（{entity_type}）")

    print("\n===== ② 语义召回（Vector Store） =====")
    for entity_type in ("metric", "column", "table", "value"):
        for entity in context.entities.get(entity_type, []):
            print(f"  [{entity_type:6s}] score={entity.score:+.4f}  {entity.label}")

    print("\n===== ③ JOIN 图补齐（Relationship） =====")
    joins = [n for n in context.notes if n.startswith("JOIN 补齐")]
    print("  " + "\n  ".join(joins) if joins else "  （无需 JOIN）")


def execute_sql(sql: str) -> tuple[list[str], list[tuple]]:
    """在业务数据库上执行 SQL。

    这里额外做一层防护：所有语句都跑在事务里并强制 rollback，
    即使 SQL 里混进了写操作也不会真的落库。
    """
    from source_models import get_source_engine

    with Session(get_source_engine()) as session:
        try:
            result = session.execute(text(sql))
            columns = list(result.keys())
            rows = [tuple(row) for row in result.fetchall()]
        finally:
            session.rollback()

    return columns, rows


def print_result(columns: list[str], rows: list[tuple]) -> None:
    if not rows:
        print("  （无数据）")
        return

    # 聚合查询在过滤后没有匹配行时会得到 NULL，展示成 NULL 比 None 更清楚
    cells = [
        [("NULL" if value is None else str(value)) for value in row] for row in rows
    ]

    widths = [
        max(len(str(columns[i])), *(len(row[i]) for row in cells))
        for i in range(len(columns))
    ]

    print("  " + " | ".join(str(columns[i]).ljust(widths[i]) for i in range(len(columns))))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in cells:
        print("  " + " | ".join(row[i].ljust(widths[i]) for i in range(len(columns))))


def answer(question: str) -> None:
    """快捷入口，等价于 `python main.py ask "..."`。

    编排逻辑在 agent.py（大模型生成 SQL + 总结），这里只做一层薄封装，
    方便直接 `python -c "from retrieval import answer; answer('...')"` 调试。
    """
    from agent import NL2SQLAgent  # 延迟导入，避免和 agent 循环依赖

    NL2SQLAgent().run(question)
