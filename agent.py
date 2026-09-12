"""大模型驱动的在线问答（第二阶段主路径）。

完整链路：

    用户问题
      ↓ ① 同义词扩展 + 向量召回 + JOIN 图      （retrieval.py，确定性）
      ↓ ② 把元数据上下文渲染成 Prompt
      ↓ ③ 大模型生成 SQL
      ↓ ④ SQL 安全校验 -> 执行（失败就把报错回灌给大模型自我修正）
      ↓ ⑤ 大模型根据结果生成中文总结
      ↓ ⑥ 打印 SQL / 结果表 / 总结

和 rule 模式的区别只有第 ③ 步：
**喂给大模型的上下文，就是规则模式里用到的那些槽位**。
所以只要前置的元数据准备做好，换谁来写 SQL 都不难。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import config
from embedding import get_embedder
from llm import LLMClient, LLMError, get_llm
from retrieval import (
    RetrievalContext,
    execute_sql,
    parse_time_range,
    print_result,
    print_trace,
    retrieve,
    select_value_filters,
)
from vector_store import get_vector_store


class SqlValidationError(RuntimeError):
    """大模型给出的 SQL 没通过安全校验。"""


# 只允许查询；哪怕推理出错也不能让写操作落到业务库上
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|attach|detach"
    r"|pragma|grant|revoke|vacuum|begin|commit)\b",
    re.IGNORECASE,
)
_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


# ===========================================================================
# 上下文渲染：把 RetrievalContext 变成 Prompt
# ===========================================================================
def render_context(context: RetrievalContext) -> str:
    """只渲染"召回并校验过"的元数据，避免把整个 schema 丢给大模型。"""
    lines: list[str] = ["【业务数据库方言】SQLite", ""]

    # ---- 指标口径 ----
    metrics = context.entities.get("metric", [])
    if metrics:
        lines.append("【可用的业务指标（口径必须严格遵守）】")
        for entity in metrics:
            payload = entity.payload
            lines.append(
                f"- {payload['business_name']}（{payload['code']}）："
                f"{payload['aggregation']}({payload['source_table']}."
                f"{payload['source_column']})"
            )
            if payload.get("description"):
                lines.append(f"    口径说明：{payload['description']}")
            if payload.get("time_table"):
                lines.append(
                    f"    默认时间字段：{payload['time_table']}."
                    f"{payload['time_column']}"
                )
            for rule in payload.get("default_filters", []):
                lines.append(
                    f"    必须保留的默认过滤：{rule['table']}.{rule['column']} "
                    f"{rule.get('operator', '=')} '{rule['value']}'"
                )
            if payload.get("synonyms"):
                lines.append(
                    f"    常见叫法：{'、'.join(payload['synonyms'])}"
                )
        lines.append("")

    # ---- 相关表与字段 ----
    if context.entities.get("table"):
        lines.append("【相关数据表】")
        for entity in context.entities["table"]:
            payload = entity.payload
            columns = "、".join(
                f"{c['physical_name']}（{c['business_name'] or c['semantic_role'] or ''}）"
                for c in payload.get("columns", [])
            )
            lines.append(
                f"- {payload['physical_name']}"
                f"（{payload['business_name'] or ''}）：{columns}"
            )
        lines.append("")

    # ---- 命中的字段 ----
    if context.entities.get("column"):
        lines.append("【命中的字段】")
        for entity in context.entities["column"]:
            payload = entity.payload
            role = payload.get("semantic_role") or ""
            lines.append(
                f"- {payload['table']}.{payload['physical_name']}"
                f"（{payload['business_name'] or ''}，语义角色：{role}）"
            )
        lines.append("")

    # ---- 取值映射 ----
    value_filters = select_value_filters(
        context.entities, context.question, context.matched_aliases
    )
    if value_filters:
        lines.append("【用户提到的取值（已做过 Value Linking）】")
        for entity in value_filters:
            payload = entity.payload
            lines.append(
                f"- 问题里的说法对应 {payload['table']}.{payload['column']} "
                f"= '{payload['value']}'"
            )
        lines.append("")

    # ---- 允许的 JOIN 关系 ----
    if context.join_graph is not None:
        edges = context.join_graph.all_edges()
        if edges:
            lines.append("【允许使用的 JOIN 关系（不要臆造其他连接条件）】")
            for edge in edges:
                lines.append(
                    f"- {edge.left_table}.{edge.left_column} = "
                    f"{edge.right_table}.{edge.right_column}"
                )
            lines.append("")

    # ---- 已识别的时间范围 ----
    time_range = parse_time_range(context.question)
    if time_range:
        lines.append(
            f"【已识别的时间范围】[{time_range[0]}, {time_range[1]})，"
            f"请用左闭右开区间表示"
        )
        lines.append("")

    # ---- 同义词命中情况 ----
    if context.matched_aliases:
        lines.append("【问题中的口语说法 -> 标准业务名】")
        for alias, canonical, entity_type in context.matched_aliases:
            lines.append(f"- {alias} -> {canonical}（{entity_type}）")
        lines.append("")

    lines.append(f"【用户问题】\n{context.question}")
    return "\n".join(lines)


SYSTEM_PROMPT = """你是一位资深数据分析师，负责把业务问题翻译成 SQL（SQLite 方言）。

你会收到一份已经治理好的元数据上下文，里面包含：业务指标口径、相关表字段、
取值映射、允许使用的 JOIN 关系。

硬性要求：
1. 只输出一条 SQL，用 ```sql 代码块包裹，不要任何解释文字。
2. 只允许 SELECT（或 WITH ... SELECT），绝对不能有 INSERT/UPDATE/DELETE/DDL。
3. 只能使用上下文中出现过的表和字段，不要臆造表名、列名。
4. 指标的聚合函数和来源字段必须严格按上下文里的口径定义书写。
5. 指标标注为"必须保留的默认过滤"的条件，任何情况下都要带上。
6. 时间过滤统一用左闭右开区间，例如
   >= '2026-08-01' AND < '2026-09-01'。
7. 如果用户问题表达了分组/排名意图（最高、最低、哪个、前 N 个、各……），
   必须 GROUP BY 对应维度并在 ORDER BY 中排名；没有分组意图就只返回一个聚合值。
8. 如果问题里给出了数量限制（如前 5 个），要加上 LIMIT。
9. 结果列请用有意义的英文别名（如 sales_amount、product_name），
   不要用中文别名。
10. JOIN 时请使用标准 INNER JOIN ... ON 写法，并给每个表起简短别名提高可读性。
"""


def build_messages(
    question: str,
    context: RetrievalContext,
    history: list[dict] | None = None,
) -> list[dict]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": render_context(context)},
    ]
    messages.extend(history or [])
    return messages


# ===========================================================================
# SQL 解析与校验
# ===========================================================================
def extract_sql(text: str) -> str:
    """从大模型回复里抠出 SQL（兼容 ```sql 代码块 / 裸文本 / 前导注释）。"""
    match = _SQL_FENCE.search(text or "")
    sql = match.group(1) if match else (text or "")

    sql = sql.strip().strip("`").strip()

    # 去掉开头的 -- 或 /* */ 注释行
    lines = [line for line in sql.splitlines()
             if not line.strip().startswith("--")]
    sql = "\n".join(lines).strip()
    while sql.startswith("/*"):
        end = sql.find("*/")
        if end < 0:
            break
        sql = sql[end + 2 :].strip()

    return sql.rstrip().rstrip(";").strip()


def validate_sql(sql: str) -> str:
    if not sql:
        raise SqlValidationError("大模型没有返回任何 SQL")

    if not re.match(r"^\s*(select|with)\b", sql, re.IGNORECASE):
        raise SqlValidationError("只允许 SELECT / WITH 查询语句")

    if ";" in sql:
        raise SqlValidationError("只允许单条语句（不能包含分号）")

    forbidden = _FORBIDDEN_SQL.search(sql)
    if forbidden:
        raise SqlValidationError(f"SQL 里出现了被禁止的关键字：{forbidden.group(0)}")

    return sql


# ===========================================================================
# Agent
# ===========================================================================
@dataclass
class AgentResult:
    question: str
    context: RetrievalContext
    sql: str | None = None
    columns: list[str] | None = None
    rows: list[tuple] | None = None
    summary: str | None = None
    attempts: int = 0
    error: str | None = None


class NL2SQLAgent:
    """把 Embedding / 向量库 / LLM 客户端都持有住，便于交互式多轮提问复用。"""

    def __init__(self, use_llm: bool = True):
        self.embedder = get_embedder()
        self.store = get_vector_store(dim=self.embedder.dim)
        self.llm: LLMClient | None = get_llm() if use_llm else None

    # -- 检索（确定性，和 rule 模式完全共用）--------------------------------
    def retrieve(self, question: str) -> RetrievalContext:
        return retrieve(question, self.embedder, self.store)

    # -- ③ 大模型生成 SQL ---------------------------------------------------
    def generate_sql(
        self,
        question: str,
        context: RetrievalContext,
        feedback: str | None = None,
        history: list[dict] | None = None,
    ) -> str:
        assert self.llm is not None
        messages = build_messages(question, context, history)

        if feedback:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"上一条 SQL 有问题，请修正后重新输出完整 SQL。\n"
                        f"问题描述：{feedback}"
                    ),
                }
            )

        return self.llm.chat(messages, temperature=0.0)

    # -- ④ 生成 -> 校验 -> 执行，失败自我修正 -------------------------------
    def generate_and_execute(
        self,
        question: str,
        context: RetrievalContext,
    ) -> tuple[str, list[str], list[tuple], int]:
        assert self.llm is not None

        history: list[dict] = []
        feedback: str | None = None
        last_error: str | None = None

        for attempt in range(1, config.LLM_MAX_ATTEMPTS + 1):
            raw = self.generate_sql(question, context, feedback, history)

            try:
                sql = validate_sql(extract_sql(raw))
            except SqlValidationError as exc:
                last_error = str(exc)
                print(f"  [第 {attempt} 次] SQL 校验未通过：{exc}")
                history = [
                    {"role": "assistant", "content": raw},
                ]
                feedback = (
                    f"SQL 校验未通过：{exc}。请严格只输出一条 SELECT 语句，"
                    f"用 ```sql 代码块包裹。"
                )
                continue

            print(f"  [第 {attempt} 次] 生成 SQL 并通过安全校验")

            try:
                columns, rows = execute_sql(sql)
                return sql, columns, rows, attempt
            except Exception as exc:  # noqa: BLE001 - 要把数据库报错回灌给大模型
                detail = str(exc).strip()
                last_error = f"{type(exc).__name__}: {detail[:600]}"
                # SQLAlchemy 的异常是多行的，控制台只打第一行，完整内容回灌给大模型
                first_line = detail.splitlines()[0] if detail else ""
                print(f"  [第 {attempt} 次] 执行报错：{type(exc).__name__}: {first_line}")

                history = [{"role": "assistant", "content": raw}]
                feedback = (
                    f"这条 SQL 在 SQLite 上执行失败，报错如下：\n{last_error}\n"
                    f"请检查表名/列名/别名和 JOIN 写法后重新给出完整 SQL。"
                )

        raise LLMError(
            f"连续 {config.LLM_MAX_ATTEMPTS} 次都没能得到可执行的 SQL：{last_error}"
        )

    # -- ⑤ 大模型总结 -------------------------------------------------------
    def summarize(
        self,
        question: str,
        sql: str,
        columns: list[str],
        rows: list[tuple],
    ) -> str:
        assert self.llm is not None

        if not rows:
            result_text = "（查询没有返回任何数据行）"
        else:
            shown = rows[: config.LLM_SUMMARY_MAX_ROWS]
            body = "\n".join(
                " | ".join("NULL" if v is None else str(v) for v in row)
                for row in shown
            )
            header = " | ".join(columns)
            result_text = f"{header}\n{body}"
            if len(rows) > len(shown):
                result_text += f"\n（共 {len(rows)} 行，只展示了前 {len(shown)} 行）"

        return self.llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是一位数据分析师。请根据用户问题、执行的 SQL 和查询结果，"
                        "用简洁的中文给出结论。要求：\n"
                        "1. 先直接回答用户的问题（给出关键数字）。\n"
                        "2. 如果结果有多行，指出排名靠前的几项及其量级对比。\n"
                        "3. 如果结果为空或为 NULL，明确说明没有符合条件的数据，"
                        "并推测可能的原因（例如时间范围内无数据）。\n"
                        "4. 不要复述 SQL，不要编造结果里没有的数据。\n"
                        "5. 不超过 120 字。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"【用户问题】\n{question}\n\n"
                        f"【执行的 SQL】\n{sql}\n\n"
                        f"【查询结果】\n{result_text}"
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=512,
        ).strip()

    # -- 完整流程 -----------------------------------------------------------
    def run(
        self,
        question: str,
        mode: str = "auto",
        want_summary: bool | None = None,
        trace: bool = True,
    ) -> AgentResult:
        context = self.retrieve(question)
        if trace:
            print_trace(context)

        want_summary = config.LLM_SUMMARY if want_summary is None else want_summary
        use_llm = mode == "llm" or (mode == "auto" and self.llm is not None)

        if not use_llm:
            return self._run_rule(question, context)

        return self._run_llm(question, context, want_summary)

    # ---- 本地规则模式（离线兜底，也是理解槽位来源的最好教具）-------------
    def _run_rule(self, question: str, context: RetrievalContext) -> AgentResult:
        result = AgentResult(question=question, context=context, sql=context.sql)

        print("\n===== ④ SQL（本地规则模式）=====")
        if not context.sql:
            print("  无法生成 SQL")
            for note in context.notes:
                print(f"  - {note}")
            result.error = "规则模式无法生成 SQL"
            return result

        print("  " + context.sql.replace("\n", "\n  "))

        print("\n===== ⑤ 在业务数据库上执行 =====")
        try:
            columns, rows = execute_sql(context.sql)
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
            print(f"  执行失败：{result.error}")
            return result

        result.columns, result.rows = columns, rows
        print_result(columns, rows)
        return result

    # ---- 大模型模式 -------------------------------------------------------
    def _run_llm(
        self,
        question: str,
        context: RetrievalContext,
        want_summary: bool,
    ) -> AgentResult:
        result = AgentResult(question=question, context=context)

        print("\n===== ④ 大模型生成 SQL =====")
        try:
            sql, columns, rows, attempts = self.generate_and_execute(
                question, context
            )
        except LLMError as exc:
            result.error = str(exc)
            print(f"  失败：{exc}")
            if context.sql:
                print("  回退到本地规则模式生成的 SQL 供参考：")
                print("  " + context.sql.replace("\n", "\n  "))
            return result

        result.sql, result.columns, result.rows, result.attempts = (
            sql, columns, rows, attempts
        )

        print("\n----- 最终 SQL -----")
        print("  " + sql.replace("\n", "\n  "))

        print("\n===== ⑤ 在业务数据库上执行 =====")
        print_result(columns, rows)

        if not want_summary:
            return result

        print("\n===== ⑥ 大模型总结 =====")
        try:
            result.summary = self.summarize(question, sql, columns, rows)
            print("  " + result.summary.replace("\n", "\n  "))
        except LLMError as exc:
            result.error = f"总结失败：{exc}"
            print(f"  总结失败：{exc}")

        return result
