# 站点工具箱

插件 ID：`sitetoolbox`

这是一个 MoviePilot 站点诊断与适配工具箱。它可以选择已有站点测试 RSS 订阅是否可以正常获取和解析，也整合了原 `siteadapter` 的站点索引和用户数据解析适配能力，并支持预览和清理下载器中的缺失文件种子。

## 功能

- 从 MoviePilot 已有站点中选择需要测试的站点。
- 优先测试站点已保存的 RSS 地址。
- 如果站点没有保存 RSS，可按 MoviePilot 内置规则尝试生成 RSS 链接。
- 可选择是否把自动获取到的 RSS 地址保存回站点配置。
- 可一键尝试修复无效 RSS：忽略 `#`、补全相对路径、重新生成 RSS 地址并写回。
- 显示最近一次测试结果：站点、域名、状态、条目数、RSS 来源、耗时和测试时间。
- 通过配置补充或覆盖站点搜索、浏览、列表字段等索引规则。
- 通过配置修正站点上传量、下载量、分享率、魔力、做种、下载等账号数据解析。
- 在详情页展示 RSS 结果、站点适配概览、规则明细和用户数据健康检查。
- 可选择一个或多个下载器，预览 qBittorrent `missingFiles` 状态的种子。
- 可按最近一次预览快照清理仍处于 `missingFiles` 的种子任务，默认不删除数据文件。
- 详情页采用概览、操作区、主表、折叠详情的工作台布局。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| 启用插件 | 开启 | 控制插件是否启用。 |
| 测试站点 | 空 | 选择需要测试 RSS 的已有站点。 |
| 请求超时 | 20 秒 | 获取 RSS 链接和解析 RSS 时的请求超时。 |
| 未配置RSS时自动获取 | 开启 | 站点没有 RSS 地址时，访问站点 RSS 页面尝试生成链接。 |
| 自动保存获取到的RSS | 关闭 | 自动获取成功后，写回站点 RSS 地址。 |
| 启用用户数据解析规则 | 开启 | 对 MoviePilot 已有站点解析器的用户数据解析结果做二次修正。 |
| 站点适配配置 | 空 | 站点规则配置。支持 JSON、JSON 数组、`domain|base64(json)` 多行格式。 |
| 缺失种子清理下载器 | 空 | 选择需要扫描和清理 `missingFiles` 种子的下载器。 |
| 同时删除数据文件 | 关闭 | 清理缺失种子时是否要求下载器同步删除数据文件。默认关闭，只删除下载器任务。 |

## 使用

1. 安装并启用 `站点工具箱`。
2. 在插件配置页选择要测试的站点并保存。
3. 打开插件详情页，点击 `测试已选站点 RSS`。
4. 关闭再打开详情页，查看最新测试结果。

也可以通过 API 触发测试：

```http
POST /api/v1/plugin/sitetoolbox/test/rss
```

测试单个站点：

```http
POST /api/v1/plugin/sitetoolbox/test/rss/{site_id}
```

尝试修复已选站点：

```http
POST /api/v1/plugin/sitetoolbox/repair/rss
```

尝试修复单个站点：

```http
POST /api/v1/plugin/sitetoolbox/repair/rss/{site_id}
```

修复会在 RSS 解析成功后写回站点配置。对于站点本身返回 `403`、`404`、Cookie 失效、站点没有生成 RSS 能力等问题，插件只会报告失败，不会强行写入无效地址。

检查用户数据完整性：

```http
POST /api/v1/plugin/sitetoolbox/check/userdata
```

检查结果会写入插件配置，并在插件详情页的 `用户数据健康检查` 表格中展示。

预览缺失文件种子：

```http
POST /api/v1/plugin/sitetoolbox/cleanup/missing/preview
```

清理最近一次预览中的缺失文件种子：

```http
POST /api/v1/plugin/sitetoolbox/cleanup/missing
```

清理接口会先按 hash 复查当前任务状态，只删除仍为 `missingFiles` 的种子；如果任务已恢复、已下载或状态变化，会跳过。

## 站点适配配置

配置格式兼容原 `站点适配器` 插件。单个站点配置由三部分组成：

```json
{
  "domain": "example.com",
  "indexer": {},
  "userdata": {}
}
```

| 字段 | 说明 |
| --- | --- |
| `domain` | 站点域名。可以写 `example.com`，也可以写完整 URL。插件会归一化成 host。 |
| `indexer` | 资源索引规则。会传给 MoviePilot 的 `SitesHelper().add_indexer(domain, indexer)`。 |
| `userdata` | 用户数据解析规则。用于修正站点管理中的用户数据字段。 |

支持直接粘贴 JSON 对象、JSON 数组、包一层 `sites` / `rules` 的对象，或多行 base64 格式：

```text
域名|配置 JSON 的 base64 编码
```

最小示例：

```json
{
  "domain": "example.com",
  "indexer": {
    "schema": "NexusPhp",
    "search": {},
    "torrents": {}
  },
  "userdata": {
    "calculate_ratio": true,
    "fields": {
      "upload": "//span[@id='uploaded']/text()",
      "download": "//span[@id='downloaded']/text()",
      "bonus": {
        "xpath": "//span[@id='bonus']/text()",
        "regex": "([0-9,.]+)",
        "type": "float"
      }
    }
  }
}
```

`userdata.fields` 支持字符串 XPath、对象规则和候选规则数组。对象规则可用参数包括 `value`、`xpath`、`attribute`、`index`、`regex`、`group`、`ignore_case`、`replace`、`type`、`only_empty`、`continue`。

常用目标字段包括 `username`、`user_level`、`userid`、`upload`、`download`、`ratio`、`bonus`、`seeding`、`leeching`、`active`、`seeding_size`。类型包括 `size`、`int`、`float`、`text`、`active`。

## 状态说明

- `正常`：RSS 可以获取并解析到至少一条资源。
- `空结果`：RSS 可以访问，但没有解析到条目。
- `异常`：站点不存在、未启用、未配置 RSS、RSS 链接过期、请求失败或解析失败。

## 注意

- RSS URL 中疑似 `passkey`、`token` 等敏感参数会在页面显示时脱敏。
- 插件不会默认修改站点配置，只有开启 `自动保存获取到的RSS` 才会写回站点 RSS 地址。
- 部分站点需要有效 Cookie 或 User-Agent；插件会复用站点配置中的 Cookie、UA、代理和超时设置。
- 站点适配配置不应写入站点 Cookie、账号、密钥等敏感信息。
- 从 `siteadapter` 迁移时，可以把原插件的 `站点适配配置` 原样复制到工具箱。
- 缺失种子清理当前面向 qBittorrent 的 `missingFiles` 状态；清理前应先查看详情页预览结果。
