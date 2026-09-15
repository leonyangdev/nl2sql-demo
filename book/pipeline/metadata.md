# 1. 元数据采集与治理

元数据阶段把业务数据库能自动提供的“物理事实”和需要人工补充的“业务语义”合并，形成 NL2SQL 的知识底座。

## 物理元数据与业务元数据

SQLAlchemy Inspector 能自动获得：

- 表名与字段名；
- 数据类型、可空性；
- 主键与外键；
- 数据库声明的引用关系。

但下面这些只能来自人工治理、指标平台或业务知识库：

- 中文业务名和说明；
- 一行数据的粒度；
- 字段是维度、度量还是时间；
- 同义词和口语叫法；
- 指标公式、默认时间字段、默认过滤；
- 哪些低基数字段允许被采样为 Value。

`business_config.py` 正是这层语义来源。

## 为什么采集必须先建 Node，再建 Edge

`collector.py` 分三遍处理：

```text
Pass 1-A  Table
Pass 1-B  Column
Pass 2    Relationship
Pass 3-A  Metric
Pass 3-B  Value
```

扫描 `sales_order_item.product_id` 时，目标 `product` 可能还未写入元数据中心。关系表需要引用两端的表 ID 和字段 ID，因此必须先创建所有 Table/Column 节点并 `flush()` 获得主键，再创建 Relationship 边。

这是构图的通用原则，不是 SQLite 的特殊限制。

## Table 与 Column

表元数据同时保存物理标识和业务解释：

```python
MetaTable(
    datasource_name="sales_db",
    schema_name="main",
    physical_name="sales_order_item",
    business_name="销售订单明细",
    business_domain="sales",
    grain="一行代表一张订单中的一个产品",
)
```

字段的 `semantic_role` 会直接影响后续查询规划：

| 角色 | 含义 | 示例 |
|---|---|---|
| `identifier` | 唯一标识 | `product.id` |
| `foreign_key` | 外部引用 | `sales_order.region_id` |
| `dimension` | 可分组/过滤的业务属性 | `product.product_name` |
| `measure` | 可参与聚合的数值 | `pay_amount`, `quantity` |
| `time` | 可用于时间过滤 | `order_date` |

## Relationship

当前数据模型产生三条核心关系：

```text
sales_order_item.order_id   → sales_order.id
sales_order_item.product_id → product.id
sales_order.region_id       → region.id
```

元数据还保存 `relationship_type`、`cardinality`、`source_type` 和 `confidence`。Demo 全部来自数据库外键，置信度为 1；生产系统还可以容纳人工关系或推断关系。

## Metric：业务口径的核心

销售额配置不是一个名字，而是一组可执行约束：

```python
"sales_amount": {
    "business_name": "销售额",
    "aggregation": "SUM",
    "source": {"table": "sales_order_item", "column": "pay_amount"},
    "time_column": {"table": "sales_order", "column": "order_date"},
    "default_filters": [{
        "table": "sales_order",
        "column": "status",
        "operator": "=",
        "value": "completed",
    }],
    "synonyms": ["销售金额", "成交额", "营业额", "卖了多少钱"]
}
```

因此只要 Metric 链接正确，就同时得到：

- 聚合函数 `SUM`；
- 来源字段 `sales_order_item.pay_amount`；
- 时间字段 `sales_order.order_date`；
- 强制过滤 `sales_order.status = 'completed'`；
- 能触发它的多种业务说法。

项目还定义了“销量”指标，来源为 `quantity`。这让检索必须在多个 Metric 中真正做选择，避免 Demo 退化成硬编码单一指标。

## Value：只治理适合枚举的字段

`region_name` 和 `status` 被显式标为 `enumerable: True`，采集器才会读取不同值并构造 `MetaValue`。`product_name` 虽然也是字符串，却不会被采样。

原因是产品名属于高基数实体。如果把每个产品都当作候选值，既会让索引膨胀，也可能把多个相似产品同时拼成互相矛盾的 `WHERE` 条件。

当前策略同时要求：

1. 业务配置显式允许枚举；
2. 字段类型是字符串；
3. 不同值数量不超过 `MAX_ENUM_VALUES`（默认 20）。

## 重建行为

`python main.py init` 会重建业务库和元数据中心，因此它适合教学与 Demo，不适合直接照搬到生产环境。生产采集应改为 Upsert、变更检测或元数据快照，避免破坏稳定 ID 与审计历史。
