# 三类数据与职责边界

当前项目有三个 SQLite 文件。它们看起来都是数据库，但承担完全不同的职责。分清这条边界，是理解整个架构的关键。

## 业务数据库：`sales.db`

它保存真正的业务事实：

```text
region (1) ───< sales_order (1) ───< sales_order_item >─── (1) product
```

| 表 | 粒度 | 关键字段 |
|---|---|---|
| `region` | 一行一个销售区域 | `region_name` |
| `product` | 一行一个产品 | `product_name`, `category` |
| `sales_order` | 一行一张订单 | `region_id`, `order_date`, `status` |
| `sales_order_item` | 一行一个订单商品明细 | `product_id`, `quantity`, `pay_amount` |

它回答“发生了什么”，但不解释字段的业务含义。

## 元数据中心：`metadata.db`

它保存“关于业务数据的知识”。

| 元数据表 | 解决的问题 | 示例 |
|---|---|---|
| `meta_table` | 这张表是什么、粒度是什么 | `sales_order_item` = 销售订单明细 |
| `meta_column` | 字段的业务名、角色、别名是什么 | `pay_amount` = 实际支付金额 |
| `meta_relationship` | 表应该怎样连接 | `order_item.order_id → order.id` |
| `meta_metric` | 指标如何计算 | 销售额 = `SUM(pay_amount)` |
| `meta_value` | 口语值如何映射到标准值 | 华南 → 华南区 |
| `retrieval_document` | 哪段文本已被索引、版本是多少 | `metric:1`, hash, version |

元数据中心是 Source of Truth。业务描述变更时，应先修改这里的来源（当前 Demo 是 `business_config.py`），然后重建相关检索文档。

## 向量库：`vector_store.db`

它只保存语义检索所需的数据：

```text
entity_key + entity_type + entity_id + content + embedding + 检索标签
```

它是 Search Index，不是事实真相源。向量命中 `metric:1` 后，系统必须回到 `metadata.db` 获取公式、时间字段和默认过滤。这样做有四个收益：

- 向量记录可以随时删除并重建；
- 业务定义只维护一份，避免双写漂移；
- 检索文本可以按模型特点调整，不影响结构化定义；
- 可通过 `entity_key` 对每次召回进行审计。

## 为什么 Relationship 不进入向量库

`sales_order_item.order_id = sales_order.id` 是结构事实，不存在“像不像”。如果把关系也交给相似度召回，会出现两种错误：

- 必需的 JOIN 因语义不相似而漏掉；
- 两个名字相似但没有关系的字段被错误连接。

当前项目把 `meta_relationship` 加载为无向邻接图，再用 BFS 找最短路径。向量检索决定目标实体，图算法决定如何到达它们。

## 为什么要有全局 `entity_key`

不同元数据表的自增 ID 会重复，例如：

```text
meta_table.id = 1
meta_metric.id = 1
```

只保存数字 `1` 无法知道命中的是哪类实体。因此检索层使用：

```text
table:1
column:12
metric:1
value:3
```

`entity_key` 同时是检索文档和向量记录的稳定主键，使增量 Upsert、回取与追踪都变得简单。

<div class="takeaway"><strong>职责口诀：</strong><code>sales.db</code> 回答“数据是什么”，<code>metadata.db</code> 回答“数据意味着什么”，<code>vector_store.db</code> 回答“当前问题可能与谁相关”。</div>
