# NL2SQL Demo —— 最小可运行架构

> 📘 配套的 VitePress 系统文档位于 [`book`](./book)。进入该目录执行
> `npm install && npm run docs:dev`，可阅读从元数据治理、语义检索、JOIN 图到
> 安全 SQL 生成的完整教程。

用 **一个依赖（SQLAlchemy）** 跑通 NL2SQL 的完整骨架，
把"元数据准备"和"在线问答"两阶段真正串起来，方便对照课程逐层理解。

- **Embedding** 用阿里云百炼线上模型 `text-embedding-v4`（配了 key 就用），
  没配 key 时退回零依赖的本地哈希向量，保证离线也能跑
- **SQL 生成 + 结果总结** 由大模型（DeepSeek）完成，同时保留不依赖大模型的
  本地规则模式，方便对照两种做法
- 所有外部调用都走标准库 `urllib`，所以依赖清单始终只有 SQLAlchemy 一项

```
业务数据库(sales.db)                          用户问题
      │                                          │
      │ ① SQLAlchemy Inspector 扫描               │ ⑥ 同义词扩展 + Embedding
      ↓                                          ↓
  物理元数据 ──┐                            向量库(vector_store.db)
              │ ② 人工业务语义                   │ ⑦ 语义召回 entity_key
              ↓                                  ↓
  Metadata Repository(metadata.db) ←──────── ⑧ 回取完整定义
   meta_table / meta_column                      │
   meta_relationship / meta_metric               │ ⑨ JOIN 图补齐缺失的表
   meta_value / retrieval_document               ↓
              │                          ⑩ 大模型生成 SQL（+ 安全校验）
              │ ③ Retrieval Document     ⑪ 在业务数据库上执行
              │ ④ Embedding              ⑫ 大模型总结查询结果
              └──→ ⑤ Vector Store
```

一句话记住职责边界：

- **Metadata Repository** 是元数据**真相源**（Source of Truth）——"它是什么"
- **Vector Store** 是**语义检索索引**（Search Index，可随时重建）——"谁最相关"
- **Relationship** 不进向量库，它靠**图遍历**决定 JOIN，不靠相似度
- **大模型**只负责最后两步（写 SQL、总结结果）；前面 ①~⑨ 全是确定性的，
  它们才是决定 NL2SQL 成败的部分

---

## 快速开始

强烈建议用虚拟环境，避免和你机器上已有的 Python 包（尤其是 numpy / torch 系列）互相干扰。

需要 **Python 3.10+**（代码里用了 `int | None` 这种写法）。

### 1. 创建并激活虚拟环境

```bash
cd nl2sql-demo
python -m venv .venv
```

```powershell
# Windows PowerShell（推荐）
.\.venv\Scripts\Activate.ps1
```

```bat
:: Windows CMD
.venv\Scripts\activate.bat
```

```bash
# macOS / Linux
source .venv/bin/activate
```

> PowerShell 如果报 `因为在此系统上禁止运行脚本`，先执行一次：
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
> （只对当前窗口生效，不会改系统设置）

激活成功后命令行前面会出现 `(.venv)`。

### 2. 安装依赖

```bash
pip install -r requirements.txt    # 核心依赖只有 SQLAlchemy
```

### 3. 配置密钥（推荐，不配也能跑）

```bash
copy .env.example .env      # Windows
# cp .env.example .env      # macOS / Linux
```

然后编辑 `.env`，一共两组 key（都配了效果最好）：

```ini
# 生成 SQL + 总结结果
DEEPSEEK_API_KEY=sk-你的key

# 线上 Embedding（阿里云百炼，可选但强烈推荐）
DASHSCOPE_API_KEY=sk-你的key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

`.env` 在**导入 `config` 时自动加载**，不需要任何初始化代码，
且已加进 `.gitignore` 不会被提交。

确认是否生效：

```bash
python main.py env
```

```
  从 .env 生效  : DEEPSEEK_API_KEY, DASHSCOPE_BASE_URL, DASHSCOPE_API_KEY
  [覆盖]        : DASHSCOPE_API_KEY（进程环境变量 35 字符 -> .env 35 字符）
  Embedding     : auto + model=text-embedding-v4 ... api_key=已配置(len=35)
  LLM           : provider=deepseek model=deepseek-chat api_key=已配置(len=35)
```

只打印 key 的长度，不打印内容。两组 key 都是**可选的**：

- 没有 Embedding key → 退回本地哈希向量（离线也能跑，只是召回质量差一档）
- 没有 LLM key → SQL 生成降级为本地规则模式

任何情况下都不会因为缺 key 直接跑不起来。

### 4. 跑起来

```bash
python main.py all          # 一键跑完整条链路
```

其他命令（都在已激活的 venv 里执行）：

```bash
python main.py env          # 查看启动时装配的配置（不打印密钥）
python main.py init         # 业务库 + 元数据中心 + 自动采集
python main.py index        # 检索文档 + Embedding + 向量索引（默认增量）
python main.py index --force  # 强制全量重建
python main.py show         # 摊开元数据中心 / 检索文档 / 向量库
python main.py check        # 检查向量索引与元数据是否同步
python main.py ask "华南区销售额最高的商品"      # 大模型生成 SQL + 总结
python main.py ask "..." --rule                # 强制走本地规则，不调用大模型
python main.py chat         # 交互式连续问答
```

用完退出虚拟环境：

```bash
deactivate
```

### Embedding：两种实现，接口一致

`embedding.py` 里两个实现只共享一个 `encode()`，上层 `indexer` / `retrieval`
一行都不用改：

| 实现 | 依赖 | 说明 |
|---|---|---|
| `DashScopeEmbedder` | 只要 API Key | **默认**。阿里云百炼 `text-embedding-v4`，走 OpenAI 兼容的 `/embeddings` |
| `HashingEmbedder` | 无 | 本地 n-gram 哈希向量，离线兜底 |

用 `NL2SQL_EMBEDDER` 切换：

```bash
NL2SQL_EMBEDDER=auto       # 默认：有 key 用线上，没 key（或接口挂了）退回本地
NL2SQL_EMBEDDER=dashscope  # 强制线上，失败直接报错
NL2SQL_EMBEDDER=hash       # 强制离线哈希
```

只要 `.env` 里有 `DASHSCOPE_API_KEY`，`auto` 就会直接用线上模型。相关变量：

```ini
NL2SQL_EMBEDDING_MODEL=text-embedding-v4   # 默认
NL2SQL_EMBEDDING_DIM=1024                  # 填 0 表示不传 dimensions
NL2SQL_EMBEDDING_BATCH_SIZE=10             # 单次请求最多几条文本
NL2SQL_EMBEDDING_API_KEY=                  # 想用别的 key 单独跑 embedding 时设置
NL2SQL_EMBEDDING_BASE_URL=
```

**召回质量的差距是数量级的**（同一批问题，同一套元数据）：

| 问题 | 哈希向量 相似度 | 线上模型 相似度 |
|---|---|---|
| `今年哪个区域卖得最好` → 销售额 | 0.029 | **0.479** |
| `哪些商品的销售收入最高` → 销售额 | 0.058 | **0.540** |
| `客户买了多少件东西` → 销量 | 0.067 | **0.497** |

第三个问题**没有任何同义词命中**，`销量` 是从语义上被选出来的 —— 本地哈希向量
永远做不到这一点。

两个实现都是"逐条文本独立计算"，不依赖任何全局语料统计，所以
**增量索引天然成立**：改几条元数据就只重算几条。

```
>>> 无变化           -> [indexer] 向量索引已是最新（26 条），跳过重建
>>> 改 1 个字段描述   -> [indexer] 增量更新完成：26 条文档 / 本次 Embedding 1 条
```

唯一需要整体重建的情况是**换了 embedding 模型**（`stored_model != embedder.name`），
因为新旧向量不在同一个语义空间里。

### 可选：换成真实 Milvus

```bash
docker run -d --name milvus -p 19530:19530 -p 9091:9091 milvusdb/milvus:latest
pip install pymilvus
```

```powershell
$env:NL2SQL_VECTOR_STORE = "milvus"      # 或写进 .env
```

---

## 目录结构与课程对应

| 文件 | 对应课程内容 |
|---|---|
| `source_models.py` | 第 2 课 · 业务数据库 `sales.db`（普通业务系统） |
| `metadata_models.py` | 第 2 课 · 元数据中心六张表 |
| `business_config.py` | 第 2 课 · 数据库告诉不了我们的业务语义 |
| `collector.py` | 第 2 课 · 自动扫描：先建 Node 再建 Edge |
| `documents.py` | 第 3 课 · Metadata Entity → Retrieval Document |
| `embedding.py` | 第 3 课 · Embedding 抽象（线上 API / 本地哈希向量兜底） |
| `vector_store.py` | 第 3 课 · 向量库抽象（本地 / Milvus） |
| `indexer.py` | 第 3 课 · content_hash + 增量索引 |
| `retrieval.py` | 第 4 课 · 在线检索 + JOIN 图 + 规则式 SQL 生成 |
| `agent.py` | 第 4 课 · 大模型生成 SQL + 自我修正 + 结果总结 |
| `llm.py` | LLM 客户端（DeepSeek，OpenAI 兼容） |
| `http_client.py` | 带重试的 POST JSON（llm / embedding 共用，纯标准库） |
| `config.py` | 路径 / 参数 / `.env` 自动加载 |
| `.env.example` | 配置模板（复制成 `.env` 后填 key） |
| `main.py` | 全链路 CLI |

---

## 每个阶段到底发生了什么

> 阶段 1~3 是**元数据准备**（离线、确定性），阶段 4 是**在线问答**（大模型参与）。
> `retrieval.py` 里的规则式 SQL 生成保留下来，作为离线兜底和教学对照。

### 阶段 1：业务库 → Metadata Repository

`collector.py` 严格分两遍，这是关键工程细节：

```
Pass 1  Table  → Column      （建节点，必须 flush 拿到自增 id）
Pass 2  Relationship         （建边：把数据库 FK 变成关系元数据）
Pass 3  Metric / Value       （语义层，主要来自业务定义而非扫描）
```

为什么不能遍历一张表时一次搞定？因为扫描 `sales_order_item` 时发现
`product_id → product.id`，但 `product` 可能还没写进元数据中心，
`target_table_id` 根本拿不到。**先建 Node，再建 Edge。**

`region.region_name` 这类"字典枚举"会被采样成 `meta_value`，但**必须由
`business_config` 显式声明 `enumerable: True`**。原因见下面"踩过的坑"。

### 阶段 2：Metadata Repository → Vector Store

```
MetaTable / MetaColumn / MetaMetric / MetaValue
        ↓  每种实体一个模板
   RetrievalDocument（entity_key = "metric:1"）
        ↓  content_hash
  retrieval_document 表（版本 / 状态 / 指纹）
        ↓  Embedding
   Vector Store（entity_key + 标量标签 + content + vector）
```

为什么要多一层 `retrieval_document` 表？因为业务描述改了要能回答：
"哪条元数据变了 → 要不要重新 Embedding → 向量库更新了没"。这是增量索引的基础。

### 阶段 3：用户问题 → SQL

以 `查询 2026 年 8 月华南区销售额最高的 5 个产品` 为例：

| 步骤 | 做了什么 | 结果 |
|---|---|---|
| ① 同义词扩展 | 用 Metadata 的 `synonyms` 反哺 Query | `产品 → 产品名称` |
| ② 语义召回 | 问题向量 vs 元数据向量 | `metric:1`、`column:xx`、`value:xx` |
| ③ 回取定义 | 拿 `entity_key` 回元数据中心查完整定义 | 拿到 `SUM(pay_amount)`、默认时间字段、默认过滤条件 |
| ④ JOIN 图 | BFS 找最短 JOIN 路径补齐 `sales_order`、`product`、`region` | 3 条 JOIN |
| ⑤ 组装 SQL | 规则 + 槽位填充 | 见下 |

```sql
SELECT
  product.product_name AS product_name,
  SUM(sales_order_item.pay_amount) AS sales_amount
FROM sales_order_item
JOIN product     ON product.id     = sales_order_item.product_id
JOIN sales_order ON sales_order.id = sales_order_item.order_id
JOIN region      ON region.id      = sales_order.region_id
WHERE sales_order.status = 'completed'          -- Metric 默认过滤
  AND sales_order.order_date >= '2026-08-01'    -- Metric 默认时间字段 + 半开区间
  AND sales_order.order_date <  '2026-09-01'
  AND region.region_name = '华南区'              -- Value Linking
GROUP BY product.product_name
ORDER BY sales_amount DESC
LIMIT 5
```

注意：每个槽位都能追溯到元数据来源，**这才是 NL2SQL 真正的难点**，
不是"让大模型写 SQL"。

### 阶段 4：大模型生成 SQL 并总结结果

`agent.py` 把上面的确定性结果渲染成 Prompt 交给大模型：

```
            RetrievalContext（阶段 3 的产物）
                     │
      ┌──────────────┴──────────────┐
      │  render_context()           │  只渲染"召回并校验过"的元数据：
      │                             │  指标口径 / 表字段 / 取值映射 /
      │                             │  JOIN 关系 / 时间范围 / 同义词命中
      └──────────────┬──────────────┘
                     ↓
              大模型生成 SQL
                     ↓
      validate_sql()：只允许单条 SELECT/WITH
      黑名单关键字 + 禁止分号 + commit 前强制 rollback
                     ↓
              在业务库执行
                     ↓
        执行失败 -> 把 SQLite 报错回灌给大模型重写
                     ↓
              大模型总结结果
```

对应代码在 `NL2SQLAgent`：

| 方法 | 作用 |
|---|---|
| `retrieve()` | 复用阶段 3，产出 `RetrievalContext` |
| `render_context()` | 元数据上下文 → Prompt（不把整个 schema 丢进去） |
| `generate_sql()` | 调大模型生成 SQL |
| `generate_and_execute()` | 生成 → 校验 → 执行，失败最多重试 `NL2SQL_LLM_MAX_ATTEMPTS` 次 |
| `summarize()` | 把问题 + SQL + 结果行交回大模型做中文总结 |

**关键点：喂给大模型的上下文，就是规则模式里用到的那些槽位。**
所以前置的元数据准备做得好，换谁来写 SQL 都不难；反之，
把整个数据库 DDL 丢给大模型，再强的模型也容易编字段名。

实测输出（`python main.py ask`）：

```sql
SELECT
    p.product_name AS product_name,
    SUM(soi.pay_amount) AS sales_amount
FROM sales_order_item AS soi
INNER JOIN sales_order AS so ON soi.order_id = so.id
INNER JOIN product AS p ON soi.product_id = p.id
INNER JOIN region AS r ON so.region_id = r.id
WHERE so.status = 'completed'
  AND so.order_date >= '2026-08-01' AND so.order_date < '2026-09-01'
  AND r.region_name = '华南区'
GROUP BY p.product_name
ORDER BY sales_amount DESC
LIMIT 5
```

```
===== ⑤ 在业务数据库上执行 =====
  product_name | sales_amount
  -------------+-------------
  智能手机 A       | 8998
  笔记本 B        | 5999

===== ⑥ 大模型总结 =====
  2026年8月华南区销售额最高的产品为智能手机A（8998）和笔记本B（5999），
  共计2条记录，未超过5条说明该月该区域仅有这两种产品的销售数据。
```

大模型一次就写对了：默认过滤条件、半开时间区间、Value Linking、
JOIN 路径、GROUP BY / ORDER BY / LIMIT 全都来自上下文，不是靠它猜的。

---

## 关键设计决策（最值得记住的几点）

1. **实体级向量化，不是"一张表一个向量"**
   Table / Column / Metric / Value 各自成为检索实体，靠 `entity_type` 区分。
   一张 200 字段的表压成一个向量，字段语义会被完全淹没。

2. **为什么既有 `entity_id` 又有 `entity_key`**
   `meta_table.id = 1` 和 `meta_metric.id = 1` 都是 `1`，无法区分。
   所以要构造 `table:1` / `metric:1` 这种全局稳定主键。

3. **Vector DB 只负责"找到谁"，Metadata DB 才负责"它是什么"**
   向量返回 `metric:1`，系统回 `metadata.db` 查完整口径。
   不要把计算公式、JOIN、Filter 全塞进向量库。

4. **Relationship 不向量化**
   `order_id → id` 是确定关系，不是"语义相似"问题。
   靠 `meta_relationship` 图遍历，而不是问 Embedding"这俩像不像"。

5. **Value Metadata 的触发条件是"用户真的提到了"，不是"向量分高"**
   用户问"哪个区域卖得最好"时，三个区域取值的向量得分都不低，
   但用户没指定区域。只看相似度就会错误生成 `region_name = '华北区'`。

6. **统一用左闭右开区间表示时间**
   `[2026-08-01, 2026-09-01)`，天然避开"8 月最后一天是 30 还是 31"的经典坑。

---

## 踩过的坑（都是真实会遇到的）

| 现象 | 根因 | 处理 |
|---|---|---|
| `product_name = 'A' AND product_name = 'B'` 查不出数据 | 把 `product_name` 这种 VARCHAR 实体名当成字典枚举采样了 | 只有显式声明 `enumerable: True` 的字段才采样成 `meta_value` |
| 同一字段多个取值同时变成 WHERE | 按字段只保留得分最高的一个取值 | `select_value_filters` 去重 |
| "哪个区域"被误判成"华北区" | 值过滤只靠向量相似度触发 | 值过滤必须由"原文/别名命中"触发 |
| `status = 'completed'` 出现两次 | Metric 默认过滤与 Value 映射撞车 | WHERE 条件去重 |
| "成交金额"被 "成交" 抢走 | 同义词子串朴素匹配 | 长别名优先 + 位置占用（生产环境应换 NER） |
| 问"成交额最高"却用了 `SUM(quantity)` | 有 2 个 Metric 时，缺 "成交额 → 销售额" 这条同义词 | 补齐 `business_config` 的同义词表 |
| 问"去年华南区的销售额"却 `GROUP BY status` | "只要找得到 dimension 字段就分组" | 分组必须由分组意图词驱动（`needs_group_by`） |
| Embedding / LLM 全部 401，`/models` 也 401 | 机器上残留了一个**过期的全局同名变量**，`load_dotenv` 默认不覆盖，静默遮蔽了 `.env` 里正确的 key | 改为 `.env` 优先（`NL2SQL_DOTENV_OVERRIDE`），并在启动横幅里打印被覆盖的键与长度 |
| 线上 embedding 突然 `404 Model not exist.`，但手动探测又正常 | `.env` 写了行内注释 `NL2SQL_EMBEDDING_MODEL=text-embedding-v4  # 备选 v3`，解析器只跳过整行注释，把 `text-embedding-v4   # 备选 v3` 整串当成了模型名 | 解析器支持行内注释（未加引号且 `#` 前有空白才算），并对非法模型名直接报错 |
| 线上接口抖动后，检索结果突然变得莫名其妙 | `auto` 模式降级成本地哈希向量，而索引还是线上模型建的 —— **两个语义空间混用**，点积纯属随机，却照样返回 Top-K | 检索前校验 `vector_store` 里登记的模型与当前 embedder 是否一致，不一致直接拒绝检索并给出修复命令 |

最后一条特别隐蔽：报错长这样

```
HTTP 401 {"error":{"message":"Invalid API-key provided..."}}
```

看起来像 key 无效，实际上 key 是好的 —— 只是**用错了那个**。
所以启动横幅现在会明确打印：

```
从 .env 生效  : DEEPSEEK_API_KEY, DASHSCOPE_BASE_URL, DASHSCOPE_API_KEY
[覆盖]        : DASHSCOPE_API_KEY（进程环境变量 35 字符 -> .env 35 字符）
```

倒数第二条最值得体会：**同义词表的质量直接决定召回质量**。
这不是代码问题，是数据治理问题 —— 这也是为什么企业 NL2SQL 项目里
"业务语义治理"花的力气往往比模型本身更多。

---

## 可以自己试的问题

```bash
python main.py ask "查询 2026 年 8 月华南区销售额最高的 5 个产品"   # 标准问法
python main.py ask "华南区成交额最高的商品"      # 全用同义词，完全不出现标准业务名
python main.py ask "今年哪个区域卖得最好"        # GROUP BY region，无值过滤
python main.py ask "2026年8月华东区销售额最高的3个产品"
python main.py ask "华北区销量最高的产品"        # 换成另一个 Metric（SUM(quantity)）
python main.py ask "去年华南区的销售额"          # 整体聚合，不该 GROUP BY
python main.py ask "华东区卖了多少钱"
```

演示数据说明：3 个区域各有一张已完成订单，华南区另外放了一张
**已取消**订单（`status='canceled'`，金额 32391 / 数量 9），
用来验证 Metric 的 `default_filters` 真的生效 —— 它必须被排除在外。
所以华南区销售额是 14997 而不是 47388。

---

## V1 的边界（下一步往哪走）

这个 Demo 故意停在"能看清架构"的位置：

- **同义词匹配是朴素子串**，应替换为 NER / 实体链接。
- **大模型只做"单轮生成 + 失败重试"，没有语义校验**。
  现在只能挡住写操作和语法错误，挡不住"能跑通但口径错"的 SQL。
  生产环境需要再加一层：`EXPLAIN` 计划审查、结果合理性检查、
  或者用"指标口径"反向校验生成的 SQL。
- **Prompt 里塞的是召回结果，召回错了大模型也跟着错**。
  所以 `TOP_K` / `PER_TYPE_TOP_K` / 同义词表的质量仍然是最关键的。
- **线上 Embedding 的批量策略还很朴素**：按 `EMBEDDING_BATCH_SIZE` 串行请求，
  没有并发、没有配额控制、没有断点续传。
  元数据上到十万级时要改成并发 + "只补没算过的" 续传。
- **向量库和 Embedding 模型绑定了**：换模型必须整体重建（`indexer` 会自动
  识别 `stored_model != embedder.name` 并全量重建，但重建成本是实打实的）。
- **Metric 只支持 `SUM(单字段)`**。像 `客单价 = 销售额 / 订单数`、
  `毛利率 = (销售额 - 成本) / 销售额` 需要 `meta_metric_dependency`
  + `meta_metric_filter` + `expression`。
- **时间解析只覆盖"数字年月 + 少数相对时间词"**。
- **多 Metric 冲突时只取向量最高分，没有消歧和反问机制**。
  更稳妥的做法是：分数接近时向用户澄清，或引入 Metric 优先级 / 默认口径。
- **多轮对话、澄清反问、SQL 校验与纠错**都还没有。
- 项目是**扁平脚本风格**（顶层模块互相直接 import），
  basedpyright 的 `reportImplicitRelativeImport` 属于误报，
  已在 `pyrightconfig.json` 里关掉该规则，其余保持 `standard` 级别。

把"前置准备阶段"吃透，后面所有这些扩展都只是往这条链路上加东西，
而不是推翻它。
