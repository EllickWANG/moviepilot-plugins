# 直搜订阅

`directsearchsubscribe` 2.0 是一个完全由插件维护的站点直搜任务管理器。它适合没有可靠 TMDB、豆瓣或 Bangumi 条目的节目，也适合只想按人工关键词和集数直接检查站点的场景。

## 设计边界

- 不创建、修改或接管 MoviePilot 系统订阅。
- 不伪造豆瓣 ID，也不修改 `SubscribeChain`。
- 搜索使用站点标题直搜，不调用 TMDB、豆瓣或 Bangumi。
- 任务、目标集数、已下载进度、候选结果和回收站都保存在插件数据中，只在插件页面显示。
- 成功添加下载后仍会写入 MoviePilot 正常的下载历史，方便下载器和后续流程工作。

插件不会迁移或删除 1.x 曾创建的系统订阅。升级后如果插件页面出现旧数据提示，请先在系统订阅页核对，再人工删除旧订阅。

## 创建任务

打开插件配置，在“创建或更新节目”中填写：

1. 节目名称和类型。类型可选电视剧或电影。
2. 电视剧可填写季、起始集、总集数，也可用 `1-12,14` 直接指定目标集数。指定目标集数优先于总集数。
3. 已经拥有的集数可填入“已有集数”，这些集数不会重复下载。
4. 搜索关键词和标题别名每行一个。搜索关键词用于站点查询，名称、别名和搜索词共同用于严格标题匹配。
5. 选择站点，留空时使用系统允许搜索的活动站点。
6. 打开“保存为插件任务”后保存配置。相同名称、类型和季的任务会更新配置并保留已有进度。

## 匹配与下载

- “必须包含”中的词必须全部命中；“排除关键词”命中任意一个即跳过。
- 填写年份时，只会排除能够从资源标题识别出且年份不同的候选；标题没有年份时不会误杀。
- 自动下载默认关闭。建议先运行一次，在插件详情页检查候选，再打开自动下载。
- 未知集数下载默认关闭。只有明确接受整包或标题集数无法识别的风险时才开启。
- 有限目标全部下载成功后任务会标记为已完成；不填总集数和指定集数时进入持续追更模式。
- 删除任务会先进入插件回收站，可以在详情页恢复。

资源成功加入下载器后，插件用站点、详情页、标题和大小生成指纹，并记录成功集数，避免定时检查重复添加。每次运行最多添加的任务数由“单次最多下载”控制。

## 定时检查

插件使用自己的 cron 服务，不依赖系统订阅调度。默认表达式为：

```text
*/30 * * * *
```

即每 30 分钟检查一次。多个任务串行执行，并按“任务间隔”暂停，减少对站点的瞬时请求。

## 页面与接口

插件详情页显示任务状态、缺失集数、最近检查结果、候选资源和回收站，并提供立即检查、暂停/恢复、自动下载开关和删除操作。

插件 API 均使用 MoviePilot 的 Bearer 鉴权：

- `GET /plugin/directsearchsubscribe/tasks`
- `POST /plugin/directsearchsubscribe/tasks`
- `PUT /plugin/directsearchsubscribe/tasks/{task_id}`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/run`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/toggle`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/auto`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/reset`
- `POST|DELETE /plugin/directsearchsubscribe/tasks/{task_id}/delete`
- `POST /plugin/directsearchsubscribe/trash/{task_id}/restore`
- `GET /plugin/directsearchsubscribe/tasks/{task_id}/results`

## 后续整理说明

本插件保证“建任务、查站、筛选、跟踪集数、加入下载器”这一段不经过外部媒体信息源。MoviePilot 的下载完成监控、自动整理、刮削属于后续独立流程；如果那些流程启用了媒体识别，仍可能按系统配置访问外部信息源。需要全程手工整理时，应同时关闭或单独配置对应的自动整理流程。
