# 代码地图

项目采用扁平脚本结构，所有 Python 模块都在根目录。阅读时建议沿数据流而不是按文件名字母顺序。

## 推荐阅读顺序

```text
source_models.py
  → business_config.py
  → metadata_models.py
  → collector.py
  → documents.py
  → embedding.py + vector_store.py
  → indexer.py
  → retrieval.py
  → agent.py + llm.py
  → main.py
```

## 文件职责

| 文件 | 核心职责 | 重点入口 |
|---|---|---|
| `source_models.py` | 定义四张业务表与演示数据 | `init_source_db()` |
| `business_config.py` | 补充数据库不知道的业务语义 | `BUSINESS_METADATA` |
| `metadata_models.py` | 定义六张元数据表 | `MetaTable` 等模型 |
| `collector.py` | 采集表、字段、关系、指标和值 | `build_metadata()` |
| `documents.py` | 元数据实体转检索文本 | `build_all_retrieval_documents()` |
| `embedding.py` | 在线/本地 Embedding 抽象 | `get_embedder()` |
| `vector_store.py` | 本地 SQLite / Milvus 向量存储 | `get_vector_store()` |
| `indexer.py` | 内容指纹、增量索引、模型切换 | `build_index()` |
| `retrieval.py` | 别名、召回、回取、JOIN 图、规则 SQL | `retrieve()` |
| `agent.py` | Prompt、校验、修正、执行与总结 | `NL2SQLAgent.run()` |
| `llm.py` | OpenAI 兼容 Chat Completions 客户端 | `get_llm()` |
| `http_client.py` | 标准库 HTTP、重试与错误包装 | `post_json()` |
| `config.py` | `.env` 加载、路径和运行参数 | 模块级配置 |
| `main.py` | 命令行编排 | `main()` |

## 按问题定位代码

### “销售额为什么是这个公式？”

从 `business_config.py` 的 `metrics.sales_amount` 开始，再看 `collector.collect_metrics()` 如何把配置写入 `MetaMetric`，以及 `retrieval.hydrate()` 如何恢复为 payload。

### “华南为什么变成华南区？”

看 `business_config.py` 的 `values.region.region_name`，然后依次看 `build_alias_index()`、`expand_question()` 与 `select_value_filters()`。

### “为什么 JOIN 了这三张表？”

先看 `collector.collect_relationships()` 如何采集外键，再看 `JoinGraph.from_metadata()`、`shortest_path()` 和 `plan()`。

### “为什么只重算一条向量？”

看 `documents.content_hash()`、`indexer.persist_documents()` 与 `build_index()` 中 `changed` / `needs_full_rebuild` 的分支。

### “LLM 出错后怎么修？”

看 `agent.generate_and_execute()`：解析与校验错误、数据库执行错误都会形成反馈，带入下一次生成。

## 三个最值得调试的对象

### `RetrievalDocument`

观察输入 Embedding 的文本是否把物理标识、业务语义和同义词表达清楚。

### `SearchHit` / `MatchedEntity`

观察向量排序是否正确，以及回取后的结构化 payload 是否完整。

### `RetrievalContext`

观察生成之前的 Metric、Value、Dimension、JOIN 与 notes。若这里错了，不应指望 LLM 在最后一步自动纠正。

## 调试入口

```bash
python main.py show
python main.py check
python main.py ask "问题" --rule
python main.py ask "问题"
```

规则模式适合定位前置语义，LLM 模式适合定位 Prompt 与生成问题。两者输出的 Trace 来自同一份上下文。
