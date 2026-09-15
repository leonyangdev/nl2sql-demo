# 全局架构

项目分为“离线准备”和“在线问答”两条链路。离线链路把数据库结构和人工业务知识变成可检索资产；在线链路只检索与当前问题有关的小块上下文，再生成 SQL。

<PipelineMap />

## 两阶段总览

```text
离线准备
sales.db ──扫描──> 物理元数据 ──业务配置──> metadata.db
                                              │
                                              ├─> Retrieval Document
                                              ├─> Embedding
                                              └─> vector_store.db

在线问答
问题 ─> 同义词扩展 ─> 向量召回 ─> 元数据回取 ─> JOIN 图补齐
     ─> SQL 生成 ─> 只读校验 ─> 执行 ─> 结果总结
```

离线阶段允许较慢，但要求结果稳定、可版本化、可重建。在线阶段要求低延迟，并且只把必要上下文交给模型。

## 模块协作关系

| 阶段 | 入口模块 | 主要输入 | 主要输出 |
|---|---|---|---|
| 演示数据 | `source_models.py` | SQLAlchemy 模型 | `sales.db` |
| 业务语义 | `business_config.py` | 人工配置 | 名称、描述、同义词、指标口径 |
| 元数据采集 | `collector.py` | 业务库 + 业务配置 | `metadata.db` 五类元数据 |
| 文档构造 | `documents.py` | 结构化元数据 | 实体级检索文本 |
| 向量化 | `embedding.py` | 检索文本 | L2 归一化向量 |
| 索引维护 | `indexer.py` | 文档 + 向量 | 增量更新后的向量库 |
| 在线检索 | `retrieval.py` | 用户问题 | `RetrievalContext` |
| SQL Agent | `agent.py` | 上下文 + LLM | SQL、结果与总结 |
| 命令入口 | `main.py` | CLI 参数 | 串联所有阶段 |

## 最重要的中间对象：RetrievalContext

`RetrievalContext` 是检索层和生成层之间的契约：

```python
@dataclass
class RetrievalContext:
    question: str
    expanded_question: str = ""
    matched_aliases: list[tuple[str, str, str]] = field(default_factory=list)
    entities: dict[str, list[MatchedEntity]] = field(default_factory=dict)
    join_steps: list[dict] = field(default_factory=list)
    join_tables: set[str] = field(default_factory=set)
    join_graph: JoinGraph | None = None
    notes: list[str] = field(default_factory=list)
    sql: str | None = None
```

它同时服务两条生成路径：

- 规则模式读取其中的 Metric、Value、Dimension、时间与 JOIN 步骤，直接拼出 SQL；
- LLM 模式用 `render_context()` 把同一份内容转成受约束 Prompt。

因此，切换 SQL 生成方式不会改变前面的元数据与检索架构。

## 为什么不把整个 Schema 塞给模型

数据库一大，完整 Schema 会带来三个问题：

1. 上下文变长，成本和延迟上升；
2. 无关表和同名字段增加歧义；
3. 物理结构仍然缺少指标口径与取值别名。

项目先召回候选实体，再从元数据中心回取完整定义，最后用关系图补齐结构上必要但语义上未必相似的表。这是一种面向元数据的 RAG，但检索对象不是业务行数据，而是“如何查询数据的知识”。

## 确定性与概率式组件的边界

| 确定性组件 | 概率式组件 |
|---|---|
| 数据库反射、外键采集 | 语义 Embedding 的相似度 |
| 指标公式和默认过滤 | LLM 对 SQL 结构的表达 |
| Value 必须显式命中 | LLM 对结果的自然语言总结 |
| BFS JOIN 路径 | 召回候选之间的语义排序 |
| SQL 只读校验 | 失败后的模型自我修正 |

设计原则是：能从治理数据或结构规则确定的，就不要交给模型猜。
