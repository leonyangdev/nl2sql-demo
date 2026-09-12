"""NL2SQL Demo —— 命令行入口。

    python main.py env                   查看启动时装配好的配置（不打印密钥）
    python main.py init                  业务库 + 元数据中心 + 采集
    python main.py index [--force]       Retrieval Document + Embedding + 向量库
    python main.py show                  查看元数据中心 / 检索文档 / 向量库
    python main.py check                 检查向量索引与元数据是否同步
    python main.py ask "问题" [--rule]   在线问答（默认走大模型）
    python main.py chat [--rule]         交互式连续问答
    python main.py all [--question ...]  一键跑完整条链路（默认）

整个项目就是这条链路：

    业务数据库 -> 物理元数据采集 -> 业务语义补充 -> Metadata Repository
                                                    -> Retrieval Document
                                                    -> Embedding
                                                    -> Vector Store
    用户问题   -> 语义召回 -> 回取完整定义 -> JOIN 图补齐
               -> 大模型生成 SQL -> 安全校验 -> 执行 -> 大模型总结

配置来源：进程环境变量优先，其次项目根目录下的 .env（启动时自动加载）。
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select, text
from sqlalchemy.orm import Session

import config
from agent import NL2SQLAgent
from collector import build_metadata
from documents import build_all_retrieval_documents
from embedding import get_embedder, resolve_embedding_api
from indexer import build_index, print_documents
from llm import LLMError, resolve_settings
from metadata_models import (
    MetaColumn,
    MetaMetric,
    MetaTable,
    MetaValue,
    get_metadata_engine,
    init_metadata_db,
)
from source_models import get_source_engine, init_source_db
from vector_store import get_vector_store

DEMO_QUESTION = "查询 2026 年 8 月华南区销售额最高的 5 个产品"

RELATIONSHIP_SQL = """
SELECT st.physical_name AS source_table,
       sc.physical_name AS source_column,
       tt.physical_name AS target_table,
       tc.physical_name AS target_column,
       r.relationship_type,
       r.cardinality,
       r.source_type
FROM meta_relationship r
JOIN meta_table  st ON r.source_table_id  = st.id
JOIN meta_column sc ON r.source_column_id = sc.id
JOIN meta_table  tt ON r.target_table_id  = tt.id
JOIN meta_column tc ON r.target_column_id = tc.id
ORDER BY st.physical_name, sc.physical_name
"""


# ---------------------------------------------------------------------------
# 启动准备
# ---------------------------------------------------------------------------
def harden_stdout() -> None:
    """Windows 控制台可能是 GBK，避免个别字符编码失败直接把程序打断。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:  # noqa: BLE001 - 拿不到就算了，不影响主流程
                pass


def print_banner() -> None:
    """启动时把装配好的配置摊开，方便确认 .env 到底有没有生效。"""
    report = config.DOTENV_REPORT

    print("=" * 72)
    print("NL2SQL Demo")
    print(f"  .env          : {report.path}"
          f"（{'已找到' if report.exists else '不存在'}）")
    print(f"  从 .env 生效  : {', '.join(report.loaded) or '（无）'}")

    # 最容易出问题的地方：机器上残留的同名全局变量遮蔽了项目 .env
    for item in report.overridden:
        print(f"  [覆盖]        : {item}")
    if report.skipped_placeholder:
        print(f"  [占位符跳过]  : {', '.join(report.skipped_placeholder)}")

    print(f"  业务数据库    : {config.SOURCE_DB_PATH.name}")
    print(f"  元数据中心    : {config.METADATA_DB_PATH.name}")
    print(f"  向量库后端    : {config.VECTOR_STORE_BACKEND}")

    if config.EMBEDDER_MODE == "hash":
        print("  Embedding     : 本地 hash-ngram（强制离线）")
    else:
        try:
            embedding_settings = resolve_embedding_api()
        except RuntimeError as exc:
            print(f"  Embedding     : 配置有误 -> {exc}")
        else:
            if embedding_settings is None:
                print(f"  Embedding     : {config.EMBEDDER_MODE}"
                      f"（未配置 Embedding Key，将退回本地 hash-ngram）")
            else:
                print(f"  Embedding     : {config.EMBEDDER_MODE} + "
                      f"{embedding_settings.describe()}")

    try:
        settings = resolve_settings()
    except LLMError as exc:
        print(f"  LLM           : 配置有误 -> {exc}")
    else:
        print(
            f"  LLM           : {settings.describe()}"
            if settings
            else "  LLM           : 未配置（SQL 生成将使用本地规则模式）"
        )
    print("=" * 72)


def build_agent(mode: str) -> NL2SQLAgent:
    """mode: llm | rule | auto"""
    return NL2SQLAgent(use_llm=(mode != "rule"))


def resolve_mode(flag_rule: bool) -> str:
    if flag_rule:
        return "rule"
    return "llm" if config.SQL_GENERATOR == "llm" else "rule"


# ---------------------------------------------------------------------------
# init：业务库 + 元数据中心
# ---------------------------------------------------------------------------
def cmd_init() -> None:
    print("\n########## 阶段 1：建立业务数据库 ##########")
    init_source_db()

    print("\n########## 阶段 2：扫描进 Metadata Center ##########")
    metadata_engine = init_metadata_db()
    build_metadata(get_source_engine(), metadata_engine)


# ---------------------------------------------------------------------------
# index：检索文档 + 向量索引
# ---------------------------------------------------------------------------
def cmd_index(force: bool = False) -> None:
    print("\n########## 阶段 3：Metadata Repository -> Vector Store ##########")
    build_index(force=force)


# ---------------------------------------------------------------------------
# show：把元数据中心摊开看
# ---------------------------------------------------------------------------
def cmd_show() -> None:
    engine = get_metadata_engine()

    with Session(engine) as session:
        engine_url = str(engine.url)

        print("\n===== META TABLE =====")
        for table in session.scalars(select(MetaTable).order_by(MetaTable.id)).all():
            print(
                f"  {table.id}  {table.physical_name:<18}"
                f"{table.business_name or '':<10}"
                f"{table.business_domain or '':<10}{table.grain or ''}"
            )

        print("\n===== META COLUMN =====")
        for column in session.scalars(
            select(MetaColumn).order_by(MetaColumn.id)
        ).all():
            print(
                f"  {column.id:<3} table_id={column.table_id:<3}"
                f"{column.physical_name:<16}"
                f"{(column.business_name or ''):<12}"
                f"{(column.semantic_role or ''):<12}"
                f"{(column.synonyms or [])}"
            )

        # 元数据中心自己也是关系模型，所以可以 JOIN 自己把 ID 翻译成人看得懂的名字
        print("\n===== META RELATIONSHIP（JOIN Meta 自己）=====")
        for row in session.execute(text(RELATIONSHIP_SQL)).fetchall():
            print(
                f"  {row.source_table}.{row.source_column}"
                f"  ->  {row.target_table}.{row.target_column}"
                f"   [{row.relationship_type}/{row.cardinality}/{row.source_type}]"
            )

        print("\n===== META METRIC =====")
        for metric in session.scalars(select(MetaMetric)).all():
            source_column = session.get(MetaColumn, metric.source_column_id)
            source_table = (
                session.get(MetaTable, source_column.table_id)
                if source_column
                else None
            )
            time_column = session.get(MetaColumn, metric.time_column_id)
            time_table = (
                session.get(MetaTable, time_column.table_id) if time_column else None
            )
            if not source_column or not source_table:
                continue

            print(f"  {metric.metric_code}  {metric.business_name}")
            print(f"    定义：{metric.description}")
            print(
                f"    计算：{metric.aggregation}"
                f"({source_table.physical_name}.{source_column.physical_name})"
            )
            if time_table and time_column:
                print(
                    f"    默认时间字段："
                    f"{time_table.physical_name}.{time_column.physical_name}"
                )
            print(f"    默认过滤：{metric.default_filters}")
            print(f"    常见叫法：{metric.synonyms}")
            print("    知识链：销售额 -> meta_metric.source_column_id"
                  " -> meta_column -> sales_order_item.pay_amount")

        print("\n===== META VALUE =====")
        for value in session.scalars(select(MetaValue).order_by(MetaValue.id)).all():
            column = session.get(MetaColumn, value.column_id)
            table = session.get(MetaTable, column.table_id) if column else None
            if not column or not table:
                continue
            print(
                f"  {table.physical_name}.{column.physical_name}"
                f" = {value.value:<12}"
                f"{(value.business_name or ''):<8}{value.synonyms or []}"
            )

        print(f"\n  元数据中心位置：{engine_url}")

    # 检索文档 + 向量库
    print_documents()

    store = get_vector_store(dim=0)
    print(f"\n===== VECTOR STORE（共 {store.count()} 条向量）=====")
    for record in store.iter_records():
        preview = " ".join(record.content.split())
        print(
            f"  {record.entity_key:<14}{record.entity_type:<8}"
            f"{preview[:60]}..."
        )


# ---------------------------------------------------------------------------
# 校验：向量索引是否和元数据同步
# ---------------------------------------------------------------------------
def cmd_check() -> None:
    with Session(get_metadata_engine()) as session:
        documents = build_all_retrieval_documents(session)

        counters: dict[str, int] = {}
        for document in documents:
            counters[document.entity_type] = counters.get(document.entity_type, 0) + 1

    store = get_vector_store(dim=0)
    stored_model = store.get_meta("embedding_model")

    # 这里会顺带探测一次线上模型（一次很轻的请求），换来最准确的诊断信息
    try:
        active_model = get_embedder().name
    except Exception as exc:  # noqa: BLE001
        active_model = f"<不可用：{type(exc).__name__}: {exc}>"

    count_ok = store.count() == len(documents)
    model_ok = bool(stored_model) and stored_model == active_model

    print("\n===== 一致性检查 =====")
    print(f"  检索文档      ：{len(documents)} 条 {counters}")
    print(f"  向量库        ：{store.count()} 条")
    print(f"  索引用的模型  ：{stored_model}")
    print(f"  当前用的模型  ：{active_model}")

    if count_ok and model_ok:
        print("  结论          ：已同步 [OK]")
    else:
        reasons = []
        if not count_ok:
            reasons.append("条数不一致")
        if not model_ok:
            reasons.append("embedding 模型不一致（两个语义空间不能混用）")
        print(f"  结论          ：不同步 [FAIL] —— {'；'.join(reasons)}")
        print("                  请执行：python main.py index")


# ---------------------------------------------------------------------------
# ask：一次问答
# ---------------------------------------------------------------------------
def cmd_ask(question: str, mode: str) -> None:
    print(f"\n########## 在线问答（SQL 生成：{mode}）##########")
    try:
        agent = build_agent(mode)
        print(f"\n问题：{question}")
        agent.run(question, mode=mode)
    except RuntimeError as exc:
        # 配置/环境类问题（缺 key、向量索引与 embedding 模型不匹配等）
        # 只需要一句清楚的提示，不需要一整个 traceback
        print(f"\n[错误] {exc}")


# ---------------------------------------------------------------------------
# chat：交互式连续问答
# ---------------------------------------------------------------------------
def cmd_chat(mode: str) -> None:
    print(f"\n########## 交互式问答（SQL 生成：{mode}）##########")
    agent = build_agent(mode)
    print("\n直接输入问题，回车/输入 exit 退出。")

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question or question.lower() in ("exit", "quit", "q"):
            break

        print(f"\n问题：{question}")
        try:
            agent.run(question, mode=mode)
        except Exception as exc:  # noqa: BLE001 - 交互模式不能因为一次异常退出
            print(f"  处理失败：{type(exc).__name__}: {exc}")

    print("已退出。")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    harden_stdout()

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="NL2SQL 最小可运行 Demo（业务库 -> 元数据中心 -> 向量库 -> 问答）",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("env", help="查看启动时装配好的配置（不打印密钥）")
    sub.add_parser("init", help="重建业务库并采集元数据")

    index_parser = sub.add_parser("index", help="生成检索文档并构建向量索引")
    index_parser.add_argument(
        "--force", action="store_true", help="强制全量重建向量索引"
    )

    sub.add_parser("show", help="查看元数据中心 / 检索文档 / 向量库")
    sub.add_parser("check", help="检查向量索引是否与元数据同步")

    ask_parser = sub.add_parser("ask", help="提出一个自然语言问题")
    ask_parser.add_argument("question", nargs="+", help="例如：华南区销售额最高的产品")
    ask_parser.add_argument(
        "--rule",
        action="store_true",
        help="强制使用本地规则生成 SQL，不调用大模型",
    )

    chat_parser = sub.add_parser("chat", help="交互式连续问答")
    chat_parser.add_argument(
        "--rule", action="store_true", help="强制使用本地规则生成 SQL"
    )

    all_parser = sub.add_parser("all", help="一键跑完整条链路")
    all_parser.add_argument("--question", default=DEMO_QUESTION, help="自定义提问")
    all_parser.add_argument(
        "--rule", action="store_true", help="强制使用本地规则生成 SQL"
    )

    args = parser.parse_args(argv)

    if args.command == "env":
        print_banner()
    elif args.command == "init":
        cmd_init()
    elif args.command == "index":
        cmd_index(force=args.force)
    elif args.command == "show":
        cmd_show()
    elif args.command == "check":
        cmd_check()
    elif args.command == "ask":
        cmd_ask(" ".join(args.question), resolve_mode(args.rule))
    elif args.command == "chat":
        cmd_chat(resolve_mode(args.rule))
    elif args.command == "all":
        cmd_init()
        cmd_index()
        cmd_check()
        print(f"\n########## 阶段 4：在线问答 ##########")
        print_banner()
        cmd_ask(args.question, resolve_mode(args.rule))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
