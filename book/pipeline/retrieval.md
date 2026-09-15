# 3. 语义召回与 JOIN 图

在线检索的目标不是直接生成 SQL，而是构造一份小而完整、经过验证的 `RetrievalContext`。当前实现依次完成同义词扩展、语义召回、元数据回取、取值确认和 JOIN 补齐。

## 第一步：同义词扩展

系统从 Metric、Column、Value 的 `synonyms` 建立别名索引。例如：

```text
成交额 → 销售额（metric）
商品   → 产品名称（column）
华南   → 华南区（value）
```

对问题：

```text
华南区成交额最高的商品
```

扩展后可能得到：

```text
华南区成交额最高的商品 销售额 产品名称
```

实现使用“长别名优先 + 字符位置占用”，避免“成交金额”先被更短的“成交”截断。它仍是朴素子串匹配，生产环境可替换成 NER 或实体链接模型。

## 第二步：向量召回

扩展后的问题被编码为查询向量，向量库返回全局 Top-K，默认 `TOP_K = 20`。随后 `hydrate()`：

1. 根据 `entity_type + entity_id` 回 `metadata.db`；
2. 恢复完整结构化定义；
3. 按类型分别排序；
4. 每类最多保留 `PER_TYPE_TOP_K = 3`。

“回取”非常重要。向量库中的文本适合搜索，生成 SQL 所需的公式、字段 ID 和过滤结构仍以元数据中心为准。

## 第三步：Value Linking 不能只看分数

用户问“今年哪个区域卖得最好”时，“华南区”“华东区”“华北区”的 Value 文档都可能与“区域”相似。如果把最高分值直接转成过滤条件，就会凭空添加：

```sql
WHERE region.region_name = '华北区'
```

因此 `select_value_filters()` 规定：只有标准值或其别名确实出现在原问题中，Value 才能进入 WHERE。

同一个字段如果有多个候选值，只保留得分最高的一个，防止出现：

```sql
product_name = 'A' AND product_name = 'B'
```

这体现了“候选召回”和“约束生效”之间的区别：向量可以给候选，但确定性条件决定候选是否有权影响 SQL。

## 第四步：选择查询根表和目标表

规则模式优先选最高分 Metric。它的来源表成为查询根表，例如：

```text
root = sales_order_item
```

目标表来自：

- Metric 的时间字段与默认过滤；
- 命中的 Value；
- 用户要求的分组维度。

示例问题会得到目标：

```text
sales_order（时间 + 状态）
region（华南区）
product（产品名称）
```

## 第五步：关系图与 BFS

`JoinGraph.from_metadata()` 把每条外键关系加入双向邻接表。双向是为了让搜索可以从任意已连接表出发；生成 SQL 时仍保留正确的等值连接列。

对每个目标表，`plan()` 从当前已连接集合中寻找最短路径：

```text
sales_order_item
  ├── product
  └── sales_order
        └── region
```

得到有序步骤：

```text
JOIN sales_order ON sales_order.id = sales_order_item.order_id
JOIN region      ON region.id = sales_order.region_id
JOIN product     ON product.id = sales_order_item.product_id
```

顺序可能不同，但每一步的新表都必须连接到已经存在于查询中的表。

## 时间、分组、排序和数量

规则生成还提供几个可解释的解析器：

| 函数 | 识别内容 | 示例输出 |
|---|---|---|
| `parse_time_range()` | 年、月和少量相对时间 | `[2026-08-01, 2026-09-01)` |
| `needs_group_by()` | 哪个、各、排名、最高等意图词 | `True` |
| `parse_direction()` | 最高/最低等方向 | `DESC` / `ASC` |
| `parse_limit()` | 前 N、N 个等表达 | `5` |

时间统一使用左闭右开区间：

```sql
order_date >= '2026-08-01'
AND order_date < '2026-09-01'
```

这样不需要判断月末是 28、29、30 还是 31 日，也适用于包含时分秒的时间戳。

## 检索层的最终产物

`retrieve()` 在一次元数据会话内完成：

```text
别名索引 → 问题扩展 → 向量搜索 → hydrate
         → JoinGraph → 规则 SQL 草案 → RetrievalContext
```

LLM 不直接接触未经筛选的全库 Schema，而只看到这份上下文中与问题相关的指标、表字段、取值和允许的 JOIN 关系。
