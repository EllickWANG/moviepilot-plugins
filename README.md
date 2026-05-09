# MoviePilot 插件市场

这个仓库用于维护自用 MoviePilot 插件。

## 插件列表

- `sourceprioritysubscribe`：订阅时优先使用 `doubanid` / `bangumiid` 对应详情，避免全部强制转换到 TMDB。

## MoviePilot 接入方式

当前 MoviePilot 的远程插件市场安装逻辑偏 GitHub API。Codeup 仓库建议先克隆到本地，再用本地插件仓库方式接入：

```bash
PLUGIN_LOCAL_REPO_PATHS=/path/to/moviepilot-plugins
```

配置后重启 MoviePilot，在插件市场中安装并启用 `sourceprioritysubscribe`。

## 目录结构

- `package.json`：基础插件索引，声明 v2 兼容。
- `package.v2.json`：MoviePilot v2 插件索引。
- `plugins/sourceprioritysubscribe`：基础插件目录。
- `plugins.v2/sourceprioritysubscribe`：MoviePilot v2 插件目录。
