import DefaultTheme from 'vitepress/theme'
import PipelineMap from './PipelineMap.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('PipelineMap', PipelineMap)
  }
}
