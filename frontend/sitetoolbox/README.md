# 站点工具箱前端

MoviePilot 的真实 Vue 联邦插件入口为 `src/components/Page.vue` 和 `Config.vue`。页面调用宿主注入的鉴权 API，不保存凭据。

```sh
npm ci
npm run dev
npm test
npm run build
```

- `http://127.0.0.1:5183/`：独立交互预览，使用 34 个站点的演示数据，写操作只修改内存。
- 先运行构建，再打开 `http://127.0.0.1:5183/federation.html`：加载实际发布的 `remoteEntry.js`，验证共享 Vue/Vuetify 以及 Page/Config 两个入口。
- 构建直接输出 `../../plugins.v2/sitetoolbox/dist/assets`，清理独立预览的运行时和字体；发布前同时同步到 `plugins/sitetoolbox`。
- `review/` 保存参考图、浏览器验收截图及设计对照；其文件不进入插件发布产物。

监控计划和连续异常判定由插件后端执行。演示 API 只用于界面预览，未连接线上站点或下载器。
