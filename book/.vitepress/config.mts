import { defineConfig } from 'vitepress'

export default defineConfig({
  lang: 'zh-CN',
  title: 'NL2SQL 工程实践',
  titleTemplate: ':title · NL2SQL 工程实践',
  description: '结合 nl2sql-demo，从业务元数据、语义检索到安全 SQL 生成，完整拆解 NL2SQL。',
  cleanUrls: true,
  lastUpdated: true,
  head: [
    ['meta', { name: 'theme-color', content: '#0b6bcb' }],
    ['link', { rel: 'icon', href: '/favicon.svg', type: 'image/svg+xml' }]
  ],
  markdown: {
    lineNumbers: true,
    theme: { light: 'github-light', dark: 'github-dark' }
  },
  themeConfig: {
    logo: '/logo.svg',
    siteTitle: 'NL2SQL 工程实践',
    nav: [
      { text: '开始学习', link: '/guide/quick-start' },
      { text: '架构', link: '/architecture/overview' },
      { text: '核心链路', link: '/pipeline/metadata' },
      { text: '完整案例', link: '/walkthrough' },
      { text: '工程化', link: '/engineering/safety' }
    ],
    sidebar: [
      {
        text: '导读',
        items: [
          { text: '快速开始', link: '/guide/quick-start' },
          { text: 'NL2SQL 到底是什么', link: '/concepts/nl2sql' }
        ]
      },
      {
        text: '系统架构',
        collapsed: false,
        items: [
          { text: '全局架构', link: '/architecture/overview' },
          { text: '三类数据与职责边界', link: '/architecture/data-layer' }
        ]
      },
      {
        text: '核心链路',
        collapsed: false,
        items: [
          { text: '1. 元数据采集与治理', link: '/pipeline/metadata' },
          { text: '2. 检索文档与向量索引', link: '/pipeline/indexing' },
          { text: '3. 语义召回与 JOIN 图', link: '/pipeline/retrieval' },
          { text: '4. SQL 生成、执行与总结', link: '/pipeline/sql-generation' }
        ]
      },
      {
        text: '从问题到答案',
        items: [
          { text: '完整案例推演', link: '/walkthrough' }
        ]
      },
      {
        text: '工程化',
        items: [
          { text: '安全与正确性', link: '/engineering/safety' },
          { text: '从 Demo 走向生产', link: '/engineering/production' }
        ]
      },
      {
        text: '参考手册',
        items: [
          { text: '代码地图', link: '/reference/code-map' },
          { text: '配置项', link: '/reference/configuration' },
          { text: '命令速查', link: '/reference/commands' },
          { text: '术语表', link: '/reference/glossary' }
        ]
      }
    ],
    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
          modal: {
            noResultsText: '没有找到相关内容',
            resetButtonTitle: '清除查询',
            footer: {
              selectText: '选择',
              navigateText: '切换',
              closeText: '关闭'
            }
          }
        }
      }
    },
    outline: { level: [2, 3], label: '本页目录' },
    lastUpdated: { text: '最后更新' },
    docFooter: { prev: '上一篇', next: '下一篇' },
    returnToTopLabel: '返回顶部',
    sidebarMenuLabel: '目录',
    darkModeSwitchLabel: '外观',
    lightModeSwitchTitle: '切换到浅色模式',
    darkModeSwitchTitle: '切换到深色模式'
  }
})
