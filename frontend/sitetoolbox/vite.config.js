import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import federation from "@originjs/vite-plugin-federation";

// 构建产物直接写入插件目录，随插件一起发布；MoviePilot 通过 get_render_mode 的 dist/assets 加载。
const outDir = fileURLToPath(
  new URL("../../plugins.v2/sitetoolbox/dist", import.meta.url),
);

export default defineConfig({
  define: {
    __STB_REMOTE_ENTRY__: JSON.stringify(`/@fs${outDir}/assets/remoteEntry.js`),
  },
  plugins: [
    vue(),
    federation({
      name: "sitetoolbox",
      filename: "remoteEntry.js",
      exposes: {
        "./Page": "./src/components/Page.vue",
        "./Config": "./src/components/Config.vue",
      },
      // vue 与 vuetify 由 MoviePilot 宿主提供单例，不打进产物，否则组件注册和主题会分裂。
      shared: {
        vue: { requiredVersion: false, generate: false },
        vuetify: { requiredVersion: false, generate: false, singleton: true },
        "vuetify/styles": {
          requiredVersion: false,
          generate: false,
          singleton: true,
        },
      },
      format: "esm",
    }),
  ],
  build: {
    outDir,
    emptyOutDir: true,
    target: "esnext",
    minify: false,
    cssCodeSplit: true,
  },
  server: {
    fs: { allow: [fileURLToPath(new URL(".", import.meta.url)), outDir] },
    port: 5183,
    cors: true,
    host: "127.0.0.1",
  },
});
