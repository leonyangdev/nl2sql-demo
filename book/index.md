---
layout: home
titleTemplate: false

hero:
  name: "NL2SQL 工程实践"
  text: "自然语言如何变成可信 SQL"
  tagline: 结合当前 Python Demo，从业务语义治理、实体级向量检索、JOIN 图规划，到受约束的 LLM 生成与结果总结，完整走通一条可解释的 NL2SQL 链路。
  image:
    src: /logo.svg
    alt: NL2SQL
  actions:
    - theme: brand
      text: 开始学习
      link: /guide/quick-start
    - theme: alt
      text: 看完整案例
      link: /walkthrough

features:
  - icon: 🧭
    title: 先懂问题，再看代码
    details: 从 NL2SQL 的真正难点讲起，分清语义解析、Schema Linking、指标口径、SQL 生成各自负责什么。
  - icon: 🧱
    title: 紧贴当前项目
    details: 每个概念都映射到项目中的 Python 模块、SQLite 数据与可运行命令，不悬空讲架构。
  - icon: 🔎
    title: 全链路可追溯
    details: 用一个真实问题追踪指标、字段值、时间范围、JOIN 路径和 SQL 槽位分别来自哪里。
  - icon: 🛡️
    title: 工程边界清晰
    details: 讲清元数据真相源与向量索引的边界、只读校验、模型空间一致性和错误自修正。
  - icon: ⚙️
    title: 在线与离线双模式
    details: 既能用 DeepSeek 生成 SQL，也能切换本地规则模式，便于学习、调试与降级。
  - icon: 🚀
    title: 面向生产演进
    details: 从当前 V1 的限制出发，给出语义层、权限、评测、可观测性和规模化索引的演进路线。
---

<PipelineMap />

## 这套 Demo 的核心判断

NL2SQL 不是“把数据库 DDL 全部丢给大模型，然后等它写出 SQL”。真正决定结果质量的，是在生成之前把四件事做对：用户说的是哪个**业务指标**、提到了哪些**维度取值**、涉及哪些**表和字段**、这些表应该怎样**确定性连接**。

当前项目把这四件事分别建模，并让每一个 SQL 片段都能追溯到元数据来源：

<div class="concept-grid">
  <div class="concept-card"><strong>指标口径</strong><p>“销售额”不是猜出来的，它被治理为 <code>SUM(sales_order_item.pay_amount)</code>，并自带已完成订单过滤。</p></div>
  <div class="concept-card"><strong>字段取值</strong><p>“华南”通过 Value Linking 对齐到 <code>region.region_name = '华南区'</code>，而不是模糊猜一个区域。</p></div>
  <div class="concept-card"><strong>结构关系</strong><p>表之间的连接来自外键元数据和 BFS 最短路径，不让向量或大模型臆造 JOIN 条件。</p></div>
</div>

<div class="takeaway"><strong>一句话记住：</strong>向量库负责“找到谁”，元数据中心负责“它是什么”，关系图负责“怎么连”，大模型只在受控上下文里负责“怎么写”。</div>

## 推荐阅读路径

第一次接触 NL2SQL，依次阅读：[NL2SQL 到底是什么](/concepts/nl2sql) → [全局架构](/architecture/overview) → [完整案例推演](/walkthrough)。

已经熟悉 RAG 或数据平台，可直接进入：[元数据采集](/pipeline/metadata) → [向量索引](/pipeline/indexing) → [语义召回与 JOIN 图](/pipeline/retrieval) → [SQL 生成](/pipeline/sql-generation)。
