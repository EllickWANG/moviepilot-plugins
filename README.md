# MoviePilot 插件市场

这个仓库用于维护自用 MoviePilot 插件。

## 接入地址

MoviePilot 私人插件市场地址：

```text
https://github.com/EllickWANG/moviepilot-plugins
```

MoviePilot v2 插件市场主要读取 `main` 分支上的 `package.v2.json`：

```text
https://raw.githubusercontent.com/EllickWANG/moviepilot-plugins/main/package.v2.json
```

发布时需要同时推送 `master` 和 `main`，避免插件市场看到旧版本。

## 当前插件

| 插件 ID | 名称 | 状态 | 说明文档 |
| --- | --- | --- | --- |
| `sourceprioritysubscribefix` | 订阅外部源优先 | 正式使用 | [README](plugins.v2/sourceprioritysubscribefix/README.md) |
| `siteadapter` | 站点适配器 | 正式使用 | [README](plugins.v2/siteadapter/README.md) |
| `sourceprioritysubscribe` | 订阅外部源优先 | 旧兼容版 | [README](plugins.v2/sourceprioritysubscribe/README.md) |

## 插件说明

### 订阅外部源优先

`sourceprioritysubscribefix` 用于让订阅、搜索、下载整理和刮削流程优先使用豆瓣或 Bangumi 来源详情，减少所有内容都被强制转到 TMDB 后带来的识别、分类和刮削问题。

主要能力：

- `doubanid` / `bangumiid` 订阅详情优先。
- Bangumi-only 订阅查重。
- Bangumi 季信息接口补齐。
- Bangumi 标题别名参与搜索和资源匹配。
- 下载与整理阶段按订阅来源修正媒体信息和二级分类。
- Bangumi NFO、图片、分集图和媒体服务器刷新补齐。

详细用法见 [plugins.v2/sourceprioritysubscribefix/README.md](plugins.v2/sourceprioritysubscribefix/README.md)。

### 站点适配器

`siteadapter` 用于通过配置修复站点索引和用户数据解析问题，不在插件里写死具体站点规则。

主要能力：

- 配置化接入站点索引规则。
- 配置化修正用户上传量、下载量、分享率、魔力、做种、下载等字段。
- 支持 JSON、JSON 数组、`domain|base64(json)` 多行配置。
- 支持 XPath、正则、属性读取、JSON 统计块解析和字段类型转换。

详细用法见 [plugins.v2/siteadapter/README.md](plugins.v2/siteadapter/README.md)。

## 目录结构

- `package.json`：基础插件索引，声明 v2 兼容。
- `package.v2.json`：MoviePilot v2 插件索引。
- `plugins/<plugin_id>`：兼容插件目录。
- `plugins.v2/<plugin_id>`：MoviePilot v2 插件目录。

正式服优先使用 `plugins.v2` 目录。兼容目录保留同名 README，方便不同安装路径下查看说明。
