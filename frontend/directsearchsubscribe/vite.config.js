import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import federation from '@originjs/vite-plugin-federation'

// 构建产物直接写入插件目录，随插件一起发布；MoviePilot 通过 get_render_mode 的 dist/assets 加载。
const outDir = fileURLToPath(new URL('../../plugins.v2/directsearchsubscribe/dist', import.meta.url))

export default defineConfig({
  plugins: [
    vue(),
    federation({
      name: 'directsearchsubscribe',
      filename: 'remoteEntry.js',
      exposes: {
        './Page': './src/components/Page.vue',
        './Config': './src/components/Config.vue',
      },
      // vue 与 vuetify 由 MoviePilot 宿主提供单例，不打进产物，否则组件注册和主题会分裂。
      shared: {
        vue: { requiredVersion: false, generate: false },
        vuetify: { requiredVersion: false, generate: false, singleton: true },
        'vuetify/styles': { requiredVersion: false, generate: false, singleton: true },
      },
      format: 'esm',
    }),
  ],
  build: {
    outDir,
    emptyOutDir: true,
    target: 'esnext',
    minify: false,
    cssCodeSplit: true,
  },
  server: {
    port: 5001,
    cors: true,
    origin: 'http://localhost:5001',
  },
})
