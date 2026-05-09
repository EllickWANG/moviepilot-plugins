# 订阅外部源优先

本插件不修改 MoviePilot 核心源码，通过运行时补丁实现：

- 订阅时传入 `doubanid`：直接读取豆瓣详情生成订阅媒体信息。
- 订阅时传入 `bangumiid`：直接读取 Bangumi 详情生成订阅媒体信息。
- 未传入 `doubanid` / `bangumiid`：保留原订阅识别逻辑。
- `/api/v1/media/seasons` 支持 `douban:` / `bangumi:` 媒体 ID 的季信息。
- 补充 Bangumi 订阅查重，避免同一个 Bangumi 条目重复订阅。

启用方式：在插件页面安装并启用 `SourcePrioritySubscribe`。
