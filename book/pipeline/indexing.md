# 2. 检索文档与向量索引

结构化元数据适合程序消费，却不一定适合语义检索。`documents.py` 先把每个元数据实体渲染成自然语言丰富的 Retrieval Document，再由 `indexer.py` 维护向量索引。

## 为什么不是“一张表一个向量”

如果把一张包含 200 个字段的表压成一个向量，少数字段的语义会被整张表的内容淹没。用户问“成交额”时，系统真正需要命中的是指标或 `pay_amount` 字段，而不是泛泛的订单明细表。

当前项目采用实体级向量化：

| 实体类型 | 一条文档代表什么 | 主要召回目标 |
|---|---|---|
| `table` | 一张表及其粒度和主要字段 | 业务主题、候选表 |
| `column` | 一个字段及其语义角色和别名 | 维度、度量、时间字段 |
| `metric` | 一个完整业务指标口径 | 聚合公式、默认过滤 |
| `value` | 一个标准字段取值及别名 | Value Linking |

Relationship 不向量化，它在在线阶段以图结构参与 JOIN 规划。

## Retrieval Document 长什么样

“销售额”会被渲染为类似文本：

```text
实体类型：业务指标

指标编码：sales_amount
指标名称：销售额
业务域：sales
指标定义：已完成销售订单产生的实际支付金额
计算方式：SUM(sales_order_item.pay_amount)
默认时间字段：sales_order.order_date
默认过滤条件：sales_order.status = completed
常见叫法：销售金额、成交金额、成交额、销售收入、营业额、卖得最好……
```

文本里同时保留物理标识和业务语言，使用户表达既能按语义命中，也能通过精确术语命中。

## Embedding 的统一接口

`embedding.py` 对上层只暴露一个抽象：

```python
class BaseEmbedder(ABC):
    name: str
    dim: int

    @abstractmethod
    def encode(self, texts: list[str]) -> list[list[float]]:
        ...
```

项目提供两个实现：

### DashScopeEmbedder

调用 OpenAI 兼容的 `/embeddings` 接口，默认模型为 `text-embedding-v4`。初始化时先发一条探测请求获得实际维度；批量结果按响应中的 `index` 排回原顺序。

### HashingEmbedder

本地字符 n-gram 哈希向量：中文使用 2/3-gram，英文数字使用整词；计数采用亚线性 TF，最后执行 L2 归一化。它零依赖、可复现、离线可用，但只能捕获字面重合，无法真正理解“买了多少件”和“销量”的语义等价。

两个实现都保证逐条文本独立计算。这一点使增量索引成立：修改一条文档不会改变其他文档的向量。

## 向量库如何计算相似度

本地 `LocalVectorStore` 使用 SQLite 保存向量 BLOB，搜索时加载记录并逐个计算点积：

```text
cosine(q, d) = q · d / (||q|| × ||d||)
```

因为 Embedding 已经 L2 归一化，`||q|| = ||d|| = 1`，所以余弦相似度等于点积 `q · d`。

对于几十到几万条元数据，暴力检索足以教学和调试。规模更大时可以切换 `MilvusVectorStore`，上层 Indexer 与 Retriever 不需要改变。

## 增量索引如何判断变化

每条 Retrieval Document 都计算 SHA-256 `content_hash` 并落入 `retrieval_document`：

```text
业务描述变化
  → 文档 content 变化
  → content_hash 变化
  → version + 1
  → embedding_status = pending
  → 只重算这一条向量
```

`build_index()` 同时计算整个语料的 `corpus_signature`。跳过构建必须同时满足：

- 没有指定 `--force`；
- 语料签名一致；
- 存储的 Embedding 模型名一致；
- 向量条数等于文档条数。

## 哪些情况必须全量重建

出现任一情况，`indexer.py` 会重算全部文档：

1. 用户显式传入 `--force`；
2. `stored_model != embedder.name`；
3. 向量条数与文档条数不一致。

换模型必须全量重建，因为不同模型甚至同一模型的不同维度不在同一个向量空间中。用 A 模型生成查询向量去搜索 B 模型生成的文档向量，得到的分数没有意义。

## 一致性检查

运行：

```bash
python main.py check
```

它会对比检索文档数、向量记录数、索引模型和当前活动模型。发现空间不一致时，在线检索也会拒绝继续，而不是返回看似正常的随机 Top-K。
