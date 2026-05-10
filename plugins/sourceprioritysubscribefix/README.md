# 订阅外部源优先

插件 ID：`sourceprioritysubscribefix`

## 用途

MoviePilot 原生订阅、搜索、下载整理和刮削流程更偏向 TMDB。这个插件用于让外部来源优先参与流程：

- 如果订阅入口带有 `doubanid`，优先使用豆瓣详情生成订阅媒体信息。
- 如果订阅入口带有 `bangumiid`，优先使用 Bangumi 详情生成订阅媒体信息。
- 如果没有 `doubanid` 或 `bangumiid`，保留 MoviePilot 原本识别逻辑。
- 对 Bangumi-only 订阅补齐标题别名、季信息、二级分类、搜索匹配、整理识别和 NFO/图片刮削。

这个插件不修改 MoviePilot 主源码，通过运行时补丁接入。停用插件或重启后会按插件状态重新挂载。

## 适用场景

- 从推荐、探索、Bangumi 日历等页面订阅动漫时，详情只有 Bangumi ID，没有可靠 TMDB ID。
- 同一部动画在站点里存在中文名、日文名、英文名、罗马音、简称等多种标题，需要提高搜索匹配率。
- 下载记录或手动整理记录里已经有订阅来源，但整理时又被识别成 TMDB 的错误条目。
- Bangumi 动画整理到媒体库后，需要生成更适合媒体服务器识别的 NFO、海报、背景图和分集缩略图。
- 需要把 Bangumi 动画按 `日番`、`国漫`、`欧美动漫`、`动漫电影` 等二级分类整理。

## 安装

在 MoviePilot 插件市场添加私人仓库：

```text
https://github.com/EllickWANG/moviepilot-plugins
```

搜索并安装：

```text
订阅外部源优先
```

确认安装的是增强版插件 ID：

```text
sourceprioritysubscribefix
```

旧插件 `sourceprioritysubscribe` 只是基础兼容版，正式服建议使用 `sourceprioritysubscribefix`。

## 配置

插件配置页目前只有一个开关：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| 启用插件 | 开启 | 开启后挂载订阅、搜索、下载、整理、刮削相关补丁；关闭后恢复原始流程。 |

修改开关后保存即可。遇到插件市场安装后未生效、版本仍旧或运行时补丁没有挂载时，建议重启 MoviePilot。

## 功能说明

### 订阅来源优先

订阅接口或前端页面传入来源 ID 时，插件会按以下顺序处理：

1. 有 `doubanid`：读取豆瓣详情，生成 `MediaInfo`。
2. 有 `bangumiid`：读取 Bangumi 详情，生成 `MediaInfo`。
3. 两者都没有：回到 MoviePilot 原识别逻辑。

创建订阅时会保存来源 ID、标题、年份、季、总集数、海报、背景图、评分、简介和二级分类等字段，避免后续只依赖 TMDB。

### Bangumi 订阅查重

MoviePilot 原生查重主要依赖 TMDB、豆瓣等字段。插件补充了 Bangumi ID 查重：

- 同一个 `bangumiid` 不会重复订阅。
- 订阅历史也会按 `bangumiid` 查重。
- Bangumi-only 条目不需要先转换成 TMDB 才能判断是否已订阅。

### 季信息接口

插件扩展了 `/api/v1/media/seasons`：

- `mediaid=bangumi:123456` 可以返回 Bangumi 条目的季信息。
- `mediaid=douban:1234567` 可以返回豆瓣条目的季信息。
- 前端订阅弹窗选择季时不再必须依赖 TMDB 季数据。

订阅日历预缓存也会优先使用 Bangumi / 豆瓣详情。外部来源订阅不会再以 `tmdbid=None` 调用 TMDB 分集接口；只有纯 TMDB 订阅才继续查询 TMDB 分集。

### 搜索与资源匹配

对 Bangumi-only 媒体，插件会在搜索前刷新 Bangumi 详情，并补齐别名：

- 中文名
- 原名
- 英文名
- 罗马音
- infobox 中的别名
- Bangumi 返回的其他名称

匹配资源时，如果 MoviePilot 原始标题匹配失败，插件会再用归一化后的 Bangumi 标题集合匹配种子标题。这个逻辑是通用标题归一化，不针对单部剧硬编码。

### 下载二级分类

下载时插件会从订阅来源和 Bangumi 标签中补齐 `media_category`，使下载目录和后续整理目录尽量保持一致。
Bangumi 下载修正只对 Bangumi-only 订阅生效，也就是订阅有 `bangumiid` 且没有 `tmdbid` / `doubanid`；已经绑定 TMDB 或豆瓣的订阅继续走对应来源，避免被 Bangumi 详情覆盖。

常见结果包括：

- `日番`
- `国漫`
- `欧美动漫`
- `动漫电影`

实际分类仍受 MoviePilot 媒体库目录、分类配置和订阅记录里的 `media_category` 影响。

### 整理识别修复

整理时插件会根据下载历史和订阅来源回填媒体信息：

- 下载历史 `note.source` 中有订阅来源时，优先信任这个历史快照。
- 手动整理传入的媒体信息与下载历史来源冲突时，优先使用下载历史来源。
- 如果下载历史缺少来源，插件会尝试按标题、年份、类型、季匹配 Bangumi-only 订阅。
- 整理成功后，会触发 Bangumi 媒体目录重刮、分集图生成和媒体服务器刷新。

这个逻辑用于修复“搜索下载时是 Bangumi 条目，但手动整理或自动整理时又被识别成另一个 TMDB 条目”的问题。

### NFO、图片和分集图

插件提供 Bangumi 元数据模块：

- 生成电视剧目录、季、分集相关 NFO。
- 使用 Bangumi 海报和背景图补齐媒体图片。
- 对缺少横版分集图的本地视频，尝试用 `ffmpeg` 截帧生成分集缩略图。
- 整理成功后自动重刮相关媒体目录。

如果容器里没有 `ffmpeg` 或 `ffprobe`，分集缩略图截帧会失败，但不会影响主视频整理。

### 插件页面

插件页面会展示：

- 插件状态和版本。
- 最近 Bangumi-only 订阅。
- 最近 Bangumi-only 来源下载。
- 最近整理失败记录。
- 可对符合条件的整理历史执行“使用订阅来源重新整理”。
- 可触发最近 Bangumi 媒体目录刷新。

## 插件 API

这些 API 需要 MoviePilot 登录态或 Bearer Token。

### 重新整理指定历史

```http
POST /api/v1/plugin/sourceprioritysubscribefix/redo/{history_id}
```

用途：对 Bangumi-only 订阅下载失败或识别错误的整理记录，按下载历史中的订阅来源重新整理。

### 刷新 Bangumi 媒体库

```http
POST /api/v1/plugin/sourceprioritysubscribefix/refresh
```

可选参数：

| 参数 | 说明 |
| --- | --- |
| `history_id` | 只刷新指定整理历史对应的媒体目录。 |
| `title` | 刷新同标题的整理历史。 |
| `limit` | 未传 `history_id` 和 `title` 时，最多检查最近多少条成功整理记录。 |

## 日志关键字

排查时可以在 MoviePilot 日志里搜索：

```text
订阅外部源优先
Bangumi
使用下载历史来源补齐整理识别
手动整理媒体与下载历史
通过Bangumi归一化标题匹配到资源
下载二级分类按订阅来源修正
Bangumi媒体库刷新
Bangumi分集缩略图
订阅日历预缓存
```

## 常见问题

### 插件安装成功但没有生效

先确认安装的是 `sourceprioritysubscribefix`，不是旧版 `sourceprioritysubscribe`。如果版本正确但行为没变化，保存一次插件配置或重启 MoviePilot。

### 插件市场显示旧版本

MoviePilot v2 读取 GitHub `main` 分支的 `package.v2.json`。发布时需要同时推送 `master` 和 `main`，否则插件市场可能仍读取旧版本。

### 搜索结果仍然很少

插件只补齐媒体详情和标题匹配，不会绕过站点自身搜索结果、站点分类、促销筛选、做种数筛选、质量规则、排除规则或订阅过滤规则。需要结合日志确认是站点没返回、MoviePilot 过滤，还是标题匹配失败。

### 整理到错误二级分类

优先检查订阅记录里的 `media_category`、下载历史来源、媒体库分类配置和 MoviePilot 分类规则。插件会尽量从订阅来源修正分类，但最终目录仍由 MoviePilot 整理流程和媒体库配置共同决定。

### 飞牛影视仍显示未知集

确认媒体目录下的 NFO、分集 NFO、分集缩略图和文件命名是否已经更新。插件整理成功后会尝试重刮并刷新媒体服务器，但媒体服务器自身缓存可能需要手动刷新或等待扫描完成。

## 注意事项

- 本插件是运行时补丁型插件，MoviePilot 核心版本大改时可能需要适配。
- 插件不会为单部剧写死规则，匹配逻辑依赖通用标题归一化、来源 ID、下载历史和订阅记录。
- Bangumi、豆瓣、TMDB 任一外部服务异常时，相关详情补齐可能失败。
- 如果同时启用其他修改订阅、搜索、整理流程的插件，可能出现补丁顺序影响。
