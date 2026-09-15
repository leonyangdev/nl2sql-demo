<script setup lang="ts">
const stages = [
  { n: '01', title: '业务库', desc: '订单、商品、区域与交易事实', tone: 'blue' },
  { n: '02', title: '元数据中心', desc: '表、字段、关系、指标与取值', tone: 'cyan' },
  { n: '03', title: '语义索引', desc: '检索文档、Embedding 与向量', tone: 'violet' },
  { n: '04', title: '在线检索', desc: '同义词、召回、回取与 JOIN 图', tone: 'amber' },
  { n: '05', title: '受控生成', desc: 'SQL 校验、执行、修正与总结', tone: 'green' }
]
</script>

<template>
  <div class="pipeline-map" aria-label="NL2SQL 五阶段流程">
    <template v-for="(stage, index) in stages" :key="stage.n">
      <div class="pipeline-stage" :class="`tone-${stage.tone}`">
        <span class="stage-number">{{ stage.n }}</span>
        <strong>{{ stage.title }}</strong>
        <small>{{ stage.desc }}</small>
      </div>
      <span v-if="index < stages.length - 1" class="pipeline-arrow" aria-hidden="true">→</span>
    </template>
  </div>
</template>
