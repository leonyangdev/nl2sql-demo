# 快速开始

本页先让项目和文档站都跑起来。主项目只需要 Python 3.10+ 与 SQLAlchemy；文档站位于 `book`，使用独立的 Node 依赖，不会影响 Python 环境。

## 1. 运行 NL2SQL Demo

在项目根目录创建虚拟环境并安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell 使用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

项目不依赖在线模型也能运行。先用本地哈希向量和规则生成模式跑通完整链路：

```bash
NL2SQL_EMBEDDER=hash python main.py all --rule
```

这条命令按顺序完成：

1. 重建 `sales.db` 并写入演示订单；
2. 扫描业务库，结合 `business_config.py` 构建 `metadata.db`；
3. 生成 Retrieval Document，计算 Embedding，写入 `vector_store.db`；
4. 检索问题相关元数据，规划 JOIN，生成并执行 SQL。

::: tip 为什么先用 `--rule`
规则模式把指标、维度、过滤、时间和 JOIN 这些“SQL 槽位”直接摊开，是理解系统最好的入口。配置大模型后，只是把最后的 SQL 拼装交给 LLM；前面的确定性链路完全复用。
:::

## 2. 配置在线模型（可选）

复制配置模板：

```bash
cp .env.example .env
```

常用配置如下：

```ini
# SQL 生成和结果总结
DEEPSEEK_API_KEY=sk-your-key

# 语义向量
DASHSCOPE_API_KEY=sk-your-key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

然后检查系统最终装配的配置。输出只显示密钥长度，不会打印密钥内容：

```bash
python main.py env
```

有 LLM Key 时，直接提问会走大模型模式：

```bash
python main.py ask "查询 2026 年 8 月华南区销售额最高的 5 个产品"
```

## 3. 启动本文档站

进入 `book` 安装依赖并启动：

```bash
cd book
npm install
npm run docs:dev
```

默认访问 `http://localhost:5173`。生产构建和本地预览命令是：

```bash
npm run docs:build
npm run docs:preview
```

## 4. 建议先观察什么

依次执行以下命令，比只看最终 SQL 更容易理解内部状态：

```bash
python main.py init
python main.py index
python main.py show
python main.py check
python main.py ask "华南区成交额最高的商品" --rule
```

重点观察：

- `show` 中“销售额”如何指向 `sales_order_item.pay_amount`；
- `region.region_name` 的标准值和“华南”等别名；
- Retrieval Document 的 `entity_key`、版本与内容指纹；
- 问答 Trace 中命中的 Metric、Value 和自动补出的 JOIN；
- 最终 SQL 是否始终保留 `sales_order.status = 'completed'`。

## 5. 三个数据库文件

| 文件 | 存什么 | 是否是真相源 |
|---|---|---|
| `sales.db` | 订单、订单明细、商品、区域 | 是，业务事实真相源 |
| `metadata.db` | 表、字段、关系、指标、取值、检索文档 | 是，元数据真相源 |
| `vector_store.db` | 文档向量及检索标签 | 否，可由元数据重建 |

下一步阅读 [NL2SQL 到底是什么](/concepts/nl2sql)，先建立正确的问题模型。
