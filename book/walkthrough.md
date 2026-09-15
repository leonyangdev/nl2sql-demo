# 完整案例推演

用项目默认问题完整追踪一次：

> 查询 2026 年 8 月华南区销售额最高的 5 个产品

最终 SQL 中的每个片段都应该有来源。下面不跳步地把这条知识链展开。

## 1. 拆解用户意图

| 片段 | 语义 |
|---|---|
| `2026 年 8 月` | 月度时间范围 |
| `华南区` | 区域维度的指定值 |
| `销售额` | 目标 Metric |
| `最高` | 按指标降序排列，同时触发分组意图 |
| `5 个` | `LIMIT 5` |
| `产品` | 分组维度 |

## 2. 同义词和语义召回

即使用户换一种说法：

```text
2026 年 8 月华南成交额最高的 5 个商品
```

别名索引也会补入：

```text
华南 → 华南区
成交额 → 销售额
商品 → 产品名称
```

向量召回负责找到 `metric`、`column`、`table` 和 `value` 候选，随后通过 `entity_key` 回取完整元数据。

## 3. Metric 展开为计算约束

`销售额` Metric 带出：

```text
聚合：SUM
来源：sales_order_item.pay_amount
时间：sales_order.order_date
默认过滤：sales_order.status = 'completed'
```

因此查询根表是 `sales_order_item`。注意，默认过滤不是用户说出来的，但它属于指标口径，必须无条件保留。

演示数据特意放了一张华南区已取消订单，金额 32391。若遗漏默认过滤，华南销售额会从正确的 14997 错算为 47388。

## 4. Value Linking

元数据确认：

```text
问题里的“华南区”
  → value entity
  → region.region_name
  → 标准值 '华南区'
```

所以产生确定性条件：

```sql
region.region_name = '华南区'
```

若问题只说“哪个区域”，则不会选择任何具体 Value，而是把 `region_name` 作为分组维度。

## 5. 时间范围

`parse_time_range()` 将“2026 年 8 月”规范化为：

```text
[2026-08-01, 2026-09-01)
```

并作用于 Metric 指定的默认时间字段：

```sql
sales_order.order_date >= '2026-08-01'
AND sales_order.order_date < '2026-09-01'
```

它不会误用同表中的 `created_at`，因为时间字段已经在 Metric 中治理。

## 6. 分组维度

“最高”说明用户要比较多个对象；“产品”命中：

```text
product.product_name
semantic_role = dimension
```

因此生成：

```sql
GROUP BY product.product_name
ORDER BY sales_amount DESC
LIMIT 5
```

对“去年华南区的销售额”这类没有比较意图的问题，则只返回一个聚合值，不应该按任何 Dimension 分组。

## 7. JOIN 图补齐

当前已知的表：

```text
根表       sales_order_item  ← Metric 来源
目标表     sales_order       ← 时间 + 默认过滤
目标表     region            ← Value
目标表     product           ← 分组维度
```

BFS 从根表找到最短合法连接：

```text
sales_order_item.order_id   = sales_order.id
sales_order.region_id       = region.id
sales_order_item.product_id = product.id
```

这里没有任何连接条件来自字段名猜测。

## 8. 最终 SQL

LLM 可能调整别名和 JOIN 顺序，但语义应等价于：

```sql
SELECT
    p.product_name AS product_name,
    SUM(soi.pay_amount) AS sales_amount
FROM sales_order_item AS soi
INNER JOIN sales_order AS so ON soi.order_id = so.id
INNER JOIN product AS p ON soi.product_id = p.id
INNER JOIN region AS r ON so.region_id = r.id
WHERE so.status = 'completed'
  AND so.order_date >= '2026-08-01'
  AND so.order_date < '2026-09-01'
  AND r.region_name = '华南区'
GROUP BY p.product_name
ORDER BY sales_amount DESC
LIMIT 5
```

## 9. 结果核对

符合条件的华南已完成订单只有 `10001`，对应两条明细：

| 产品 | 数量 | 销售额 |
|---|---:|---:|
| 智能手机 A | 2 | 8998 |
| 笔记本 B | 1 | 5999 |

因此结果排序为智能手机 A、笔记本 B。只有两条记录并不是 LIMIT 失效，而是满足过滤条件的产品只有两个。

## 10. 可追溯性清单

| SQL 片段 | 来源 |
|---|---|
| `SUM(soi.pay_amount)` | `meta_metric.source_column_id` |
| `so.status = 'completed'` | `meta_metric.default_filters` |
| `so.order_date` | `meta_metric.time_column_id` |
| 月份上下界 | `parse_time_range()` |
| `r.region_name = '华南区'` | `meta_value` + 原文命中 |
| `p.product_name` | `meta_column` + `dimension` 角色 |
| 三个 `JOIN ... ON` | `meta_relationship` + BFS |
| `DESC` / `LIMIT 5` | 问题意图解析 |

<div class="takeaway"><strong>案例的真正结论：</strong>大模型可以写出不同风格的 SQL，但不能改变指标口径、值映射、时间字段和合法 JOIN。可靠性来自约束链，不来自某一次模型“恰好写对”。</div>
