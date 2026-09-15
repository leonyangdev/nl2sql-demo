# 术语表

## Business Metadata

数据库结构无法表达的业务知识，例如中文名称、数据粒度、语义角色、同义词和指标口径。当前来源是 `business_config.py`。

## Embedding

把文本映射为固定维度数值向量的过程。语义越接近，向量通常越相似。

## Entity Key

跨元数据类型唯一的检索主键，例如 `metric:1`、`column:12`。用于向量 Upsert、元数据回取与审计。

## Hydration / 回取

向量召回只返回候选标识后，再从元数据真相源加载完整结构化定义的过程。

## JOIN Graph

以业务表为节点、已知关系为边构成的图。当前项目用 BFS 计算从指标来源表到目标表的最短路径。

## L2 归一化

将向量缩放到欧氏长度为 1。两个归一化向量的余弦相似度可直接用点积计算。

## Metric / 指标

带有业务定义的度量口径，不只是一个数值字段。包括聚合、来源字段、时间字段、默认过滤和同义词。

## NL2SQL

Natural Language to SQL，把自然语言数据问题转换为 SQL 的任务。可靠系统还需要语义链接、查询规划、安全执行与结果解释。

## Retrieval Document

由结构化元数据渲染出的检索文本，是元数据与 Embedding 之间的中间层。它有内容指纹、版本和索引状态。

## Schema Linking

将用户语言中的业务概念链接到实际表、字段、指标和字段值的过程。

## Semantic Role

字段在分析中的语义角色，如 `identifier`、`foreign_key`、`dimension`、`measure`、`time`。

## Source of Truth

某类信息的权威来源。项目中 `sales.db` 是业务事实真相源，`metadata.db` 是元数据真相源；向量库不是。

## Value Linking

将用户提到的实体或枚举值映射到字段标准值，例如“华南”映射为 `region.region_name = '华南区'`。

## 向量空间一致性

查询向量和文档向量必须由同一模型、同一维度和兼容配置生成。否则相似度没有可比意义。

## 左闭右开区间

包含开始、不包含结束的时间区间，写作 `[start, end)`。查询 8 月时使用 `>= 8 月 1 日 AND < 9 月 1 日`。
