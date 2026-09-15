# 配置项

配置在导入 `config.py` 时一次性装配。默认项目根目录 `.env` 优先于同名进程环境变量，可通过 `NL2SQL_DOTENV_OVERRIDE=0` 恢复“进程变量优先”。

## `.env` 加载规则

- 空行和以 `#` 开头的行被忽略；
- 支持可选的 `export KEY=value`；
- 占位符如 `sk-xxxx`、`<your-key>` 会被忽略；
- 未加引号的值支持空格后行内注释；
- 值本身含 `#` 时应使用引号；
- 启动报告只展示键名和长度，不展示密钥。

## Embedding

| 配置 | 默认值 | 说明 |
|---|---|---|
| `NL2SQL_EMBEDDER` | `auto` | `auto` / `dashscope` / `hash` |
| `DASHSCOPE_API_KEY` | 空 | 默认在线 Embedding Key |
| `DASHSCOPE_BASE_URL` | DashScope 兼容端点 | 默认在线端点 |
| `NL2SQL_EMBEDDING_API_KEY` | 空 | 独立覆盖 Embedding Key |
| `NL2SQL_EMBEDDING_BASE_URL` | 空 | 独立覆盖 Embedding 端点 |
| `NL2SQL_EMBEDDING_MODEL` | `text-embedding-v4` | 向量模型 |
| `NL2SQL_EMBEDDING_DIM` | `1024` | 期望维度；`0` 表示不传 |
| `NL2SQL_EMBEDDING_BATCH_SIZE` | `10` | 每批文本数量 |
| `NL2SQL_EMBEDDING_TIMEOUT` | `60` | 请求超时秒数 |

`auto` 在没有 Key 或在线接口不可用时退回本地哈希向量。若索引原本由在线模型生成，退回后必须重建索引，否则在线检索会因模型空间不一致而拒绝运行。

## LLM

| 配置 | 默认值 | 说明 |
|---|---|---|
| `NL2SQL_LLM_PROVIDER` | 自动探测 | `deepseek` / `dashscope` |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | 兼容端点 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名 |
| `DASHSCOPE_MODEL` | `qwen-plus` | DashScope Chat 模型 |
| `NL2SQL_LLM_TEMPERATURE` | `0` | SQL 生成温度 |
| `NL2SQL_LLM_TIMEOUT` | `60` | 请求超时秒数 |

自动探测时优先 DeepSeek，再尝试 DashScope。没有任何 LLM Key 时，SQL 生成自动降级为本地规则模式。

## 在线链路

| 配置 | 默认值 | 说明 |
|---|---|---|
| `NL2SQL_SQL_GENERATOR` | `llm` | `llm` / `rule` |
| `NL2SQL_LLM_SUMMARY` | `1` | 是否让 LLM 总结结果 |
| `NL2SQL_LLM_MAX_ATTEMPTS` | `3` | SQL 生成与修正的最大总次数 |
| `NL2SQL_LLM_SUMMARY_MAX_ROWS` | `50` | 总结时回传的最大结果行数 |

命令行 `--rule` 的优先级高于 `NL2SQL_SQL_GENERATOR`。

## 向量存储

| 配置 | 默认值 | 说明 |
|---|---|---|
| `NL2SQL_VECTOR_STORE` | `local` | `local` / `milvus` |
| `MILVUS_HOST` | `localhost` | Milvus 主机 |
| `MILVUS_PORT` | `19530` | Milvus 端口 |

本地后端使用 SQLite + 暴力点积；Milvus 后端创建 COSINE/HNSW 索引。

## 代码级检索参数

下面几项目前直接定义在 `config.py`，不是环境变量：

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `TOP_K` | 20 | 向量库全局候选数 |
| `PER_TYPE_TOP_K` | 3 | 每种实体回取后保留数 |
| `MIN_SCORE` | 0.0 | 最低相似度 |
| `MAX_ENUM_VALUES` | 20 | 低基数值采样上限 |

生产环境应将它们配置化，并通过离线评测而不是主观感觉调参。
