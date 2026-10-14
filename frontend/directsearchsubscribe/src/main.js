// 本地预览入口：只用于 `pnpm dev` 时脱离 MoviePilot 查看页面，联邦产物不包含它。
import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import 'vuetify/styles'
import App from './App.vue'

const vuetify = createVuetify({
  components,
  directives,
  theme: { defaultTheme: 'light' },
})

createApp(App).use(vuetify).mount('#app')
