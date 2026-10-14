# 直搜订阅

`directsearchsubscribe` 3.x 是一个完全由插件维护的站点直搜任务管理器。它适合没有可靠 TMDB、豆瓣或 Bangumi 条目的节目，也适合只想按人工关键词和集数直接检查站点的场景。

3.0 起页面与配置均由插件自带的 Vue 联邦组件绘制，需要 MoviePilot v2.4 以上的前端支持（v3 已验证）。

## 设计边界

- 不创建、修改或接管 MoviePilot 系统订阅。
- 不伪造豆瓣 ID，也不修改 `SubscribeChain`。
- 搜索使用站点标题直搜，不调用 TMDB、豆瓣或 Bangumi。
- 任务、目标集数、已下载进度、候选结果和回收站都保存在插件数据中，只在插件页面显示。
- 成功添加下载后仍会写入 MoviePilot 正常的下载历史；下载完成后复用 MoviePilot 的目录匹配、转移和重命名能力，但媒体与剧集信息直接来自插件任务。

插件不会迁移或删除 1.x 曾创建的系统订阅。升级后如果插件页面出现旧数据提示，请先在系统订阅页核对并人工删除旧订阅，然后点击提示里的“清除旧版映射”移除插件内的残留记录。

## 页面结构

插件详情页是唯一的任务管理入口，配置页只保留全局运行参数。

- 顶部统计：任务总数、追更中、自动下载、需要关注。
- 运行状态条：定时检查周期、丢失补偿周期、最近一次补偿结果；可直接“立即补偿”或“重试失败整理”。
- 任务列表：每个任务一张卡片，显示类型、季、分类、进度条、缺集、最近一次检查结果和整理状态；支持按名称、关键词搜索，按状态筛选。
- 任务操作：立即检查、编辑、暂停/恢复、自动下载开关、查看候选与日志；更多菜单里有重置进度、清理任务（保留文件或连同下载文件）、移入回收站、彻底删除。
- 回收站：折叠显示，可恢复、单条彻底删除或清空。
- 新建/编辑任务在同一个弹窗里完成，分“节目范围 → 搜索匹配 → 下载整理”三段，站点和下载器直接从系统读取可选项。

初次创建任务时建议先保持自动下载关闭，点击“立即检查”并在候选列表里确认结果后再开启。

## 任务字段

1. 节目名称和类型。类型可选电视剧或电影。
2. 电视剧可填写季、起始集、结束集，也可用 `1-12,14` 直接指定目标集数。指定目标集数优先于起始集和结束集；都不填时进入持续追更模式。
3. “已经拥有的集数”不会重复下载。
4. 站点搜索词和标题别名每行一个。搜索词用于站点查询；名称、别名和搜索词共同用于严格标题匹配。
5. 站点留空时使用系统允许搜索的活动站点。
6. 相同名称、类型和季的任务视为同一任务，不会重复创建。

## 匹配与下载

- “必须包含”中的词必须全部命中；“排除关键词”命中任意一个即跳过。
- 填写年份时，只会排除能够从资源标题识别出且年份不同的候选；标题没有年份时不会误杀。
- 自动下载默认关闭。建议先运行一次，在候选列表检查结果，再打开自动下载。
- 默认按做种数优先；每个任务也可改为综合、免费、发布时间、小体积或大体积优先，并可设置最低做种数。
- “优先整包下载”默认开启。明确识别为 `E01-E08` 一类的整包只要覆盖任一缺集，就优先于覆盖数相同的单集资源，并下载整包全部文件。
- 下载历史去重默认开启：同时检查活动任务、回收站和 MoviePilot 下载历史；相同发布标题只下载一次，并从匹配的历史记录恢复已有集数。
- 未知集数下载默认关闭。开启后每个任务也只允许选择一个未知集数资源。
- 电影任务每轮最多成功添加一个资源。
- 有限目标全部下载成功后任务会标记为已完成。
- 删除任务会先进入回收站，可以恢复，也可以单条彻底删除或清空回收站；彻底删除后其下载记录不再参与历史去重。

候选的标题、季、集数、包含/排除词、最低做种数、去重、选集、下载器返回值和整理结果都会写入任务详细日志。日志不保存种子下载地址、Cookie 或 Passkey，每个任务最多保留 500 条。

## 清理并重新处理

- 任务“更多”菜单提供“清理任务（保留文件）”和“清理任务和下载文件”，确认后立即执行并重新检查。
- 清理范围只包含与当前节目匹配、且明确由本插件创建的下载 Hash；不会删除其他来源的下载，也不会删除媒体库成品。
- 清理后插件下载记录与进度恢复为人工填写的“已有集数”，旧 Hash 会从本任务的历史恢复中临时排除。

资源成功加入下载器后，插件同时记录站点指纹和跨站点发布标识；下载器返回“任务已存在”时只登记为重复跳过。下载任务会同时保留 MoviePilot 系统标签和“直搜订阅”标签，因此可在 MoviePilot 下载管理中正常显示。

## 下载完成后整理

- MoviePilot 检测到本插件下载完成后，插件会按下载 Hash 接管该任务的整理输入；其他来源的下载完全不受影响。
- 节目名称、类型、年份、季号、集数和二级分类直接使用插件任务中的人工数据，不请求 TMDB、豆瓣或 Bangumi。
- 文件集数仍从下载文件名本地解析；单集资源在文件名解析失败时，会使用下载记录中的唯一集数兜底。
- 整理目标目录、转移方式和重命名格式继续使用 MoviePilot 的媒体库目录配置；元数据刮削对这些下载强制关闭。
- 下载记录会显示等待整理、已加入队列、整理完成或整理失败状态。任务即使已移入回收站，已开始的下载完成后仍可按原记录整理。
- 升级时会自动重新提交旧版本因“未识别媒体信息”留下的失败记录；页面也提供“重试失败整理”。

## 定时检查与丢失补偿

插件使用自己的 cron 服务，默认每 30 分钟检查一次；多个任务串行执行并按“任务间隔”暂停。

丢失补偿默认每 6 小时对已整理的电视剧文件做一次三级对账，并保留 30 分钟整理宽限期：成品缺索引时只刷新媒体库；成品丢失但缓存仍在时重新整理；成品和缓存都丢失时恢复为缺集并重新直搜。周期、宽限时间和总开关在配置页修改。

## 接口

插件 API 均使用 MoviePilot 的 Bearer 鉴权：

- `GET /plugin/directsearchsubscribe/overview`
- `GET /plugin/directsearchsubscribe/options`
- `GET /plugin/directsearchsubscribe/tasks`
- `POST /plugin/directsearchsubscribe/tasks`
- `PUT /plugin/directsearchsubscribe/tasks/{task_id}`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/run`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/toggle`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/auto`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/reset`
- `POST /plugin/directsearchsubscribe/tasks/{task_id}/cleanup`（body：`{"delete_files": bool}`）
- `POST|DELETE /plugin/directsearchsubscribe/tasks/{task_id}/delete`
- `POST /plugin/directsearchsubscribe/trash/{task_id}/restore`
- `POST|DELETE /plugin/directsearchsubscribe/trash/{task_id}/purge`
- `POST|DELETE /plugin/directsearchsubscribe/trash/purge`
- `POST|DELETE /plugin/directsearchsubscribe/legacy/purge`
- `GET /plugin/directsearchsubscribe/tasks/{task_id}/results`
- `GET /plugin/directsearchsubscribe/tasks/{task_id}/logs`
- `POST /plugin/directsearchsubscribe/transfers/retry-failed`
- `GET /plugin/directsearchsubscribe/repair/status`
- `POST /plugin/directsearchsubscribe/repair/run`

## 前端构建

前端源码在仓库根目录 `frontend/directsearchsubscribe/`，构建产物输出到插件目录 `dist/assets/` 并随插件一起提交：

```bash
cd frontend/directsearchsubscribe
pnpm install
pnpm build
```

## 使用前提

自动整理仍依赖 MoviePilot 已配置下载目录与媒体库目录，并要求下载目录使用下载器监控。插件只跳过外部媒体识别和刮削，不改变用户现有的硬链接、复制、移动、覆盖和重命名规则。
