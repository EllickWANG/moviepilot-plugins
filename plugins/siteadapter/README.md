# 站点适配器

插件 ID：`siteadapter`

## 用途

这个插件用于把站点索引规则和站点用户数据解析规则做成可配置能力，避免为了单个站点页面结构变化就修改 MoviePilot 主源码。

它主要处理两类问题：

- 资源索引：站点搜索、浏览、列表字段解析异常时，用配置补充或覆盖索引规则。
- 用户数据：站点上传量、下载量、分享率、魔力、做种、下载等账号数据解析异常时，用配置补充解析规则。

插件不写死具体站点规则。HDcity、观众或其他站点都通过配置接入。

## 安装

在 MoviePilot 插件市场添加私人仓库：

```text
https://github.com/EllickWANG/moviepilot-plugins
```

搜索并安装：

```text
站点适配器
```

插件 ID：

```text
siteadapter
```

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| 启用插件 | 开启 | 开启后加载站点索引配置，并按配置挂载用户数据解析补丁。 |
| 启用用户数据解析规则 | 开启 | 开启后对 MoviePilot 已有站点解析器的用户数据解析结果做二次修正。 |
| 站点适配配置 | 空 | 站点规则配置。支持 JSON、JSON 数组、`domain|base64(json)` 多行格式。 |

保存配置后插件会重新加载规则。复杂规则修改后，如果发现站点管理页面仍显示旧结果，可以重启 MoviePilot 或重新触发站点数据刷新。

## 配置格式

### 多行 base64 格式

每行一个站点：

```text
域名|配置 JSON 的 base64 编码
```

适合从旧的 `自定义索引站点` 配置迁移，也适合避免多行 JSON 被前端输入框格式化破坏。

### 直接 JSON 格式

可以直接粘贴单个站点对象：

```json
{
  "domain": "example.com",
  "indexer": {
    "schema": "NexusPhp",
    "search": {},
    "torrents": {}
  },
  "userdata": {
    "fields": {
      "upload": "//span[@id='uploaded']/text()"
    }
  }
}
```

也可以粘贴数组：

```json
[
  {
    "domain": "site-a.example",
    "indexer": {
      "schema": "NexusPhp"
    }
  },
  {
    "domain": "site-b.example",
    "userdata": {
      "fields": {
        "ratio": "//span[@class='ratio']/text()"
      }
    }
  }
]
```

也支持包一层 `sites` 或 `rules`：

```json
{
  "sites": [
    {
      "domain": "example.com",
      "userdata": {
        "fields": {
          "bonus": "//span[@id='bonus']/text()"
        }
      }
    }
  ]
}
```

## 站点配置结构

单个站点配置由三部分组成：

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

`indexer` 和 `userdata` 可以只写一个。只修复用户数据时不需要提供 `indexer`。

## indexer 规则

`indexer` 兼容 MoviePilot 原 `自定义索引站点` 的规则结构，插件不重新解释字段，只负责按域名加载到 MoviePilot 的站点索引配置中。

最小示例：

```json
{
  "domain": "example.com",
  "indexer": {
    "schema": "NexusPhp",
    "search": {},
    "torrents": {}
  }
}
```

实际字段取决于站点页面和 MoviePilot 当前支持的索引规则，例如搜索 URL、列表选择器、标题、下载链接、详情链接、大小、发布时间、做种数、下载数、促销等。

## userdata 规则

`userdata` 用于解析站点管理里的账号数据。完整结构：

```json
{
  "userdata": {
    "fields": {},
    "json_stats": [],
    "calculate_ratio": false
  }
}
```

### fields

`fields` 是最常用的配置。key 是目标字段，value 是提取规则。

字符串写法等价于 XPath：

```json
{
  "userdata": {
    "fields": {
      "upload": "//span[@id='uploaded']/text()",
      "download": "//span[@id='downloaded']/text()"
    }
  }
}
```

对象写法可以指定更多参数：

```json
{
  "userdata": {
    "fields": {
      "bonus": {
        "xpath": "//span[@id='bonus']/text()",
        "regex": "([0-9,.]+)",
        "group": 1,
        "type": "float"
      }
    }
  }
}
```

同一个字段可以给多个候选规则，插件会按顺序尝试：

```json
{
  "userdata": {
    "fields": {
      "upload": [
        "//span[@id='uploaded']/text()",
        {
          "xpath": "//i[starts-with(@title,'Uploaded')]/following-sibling::text()[1]",
          "type": "size"
        }
      ]
    }
  }
}
```

### 单条提取规则参数

| 参数 | 说明 |
| --- | --- |
| `value` | 固定值。调试或站点字段固定时使用。 |
| `xpath` | 从 HTML 中提取节点、文本或属性。 |
| `attribute` | XPath 命中元素时读取哪个属性。可用 `text`、`tail`、`html` 或实际属性名。 |
| `index` | XPath 命中多个结果时取第几个，默认 `0`，也可写 `last`。 |
| `regex` | 对提取结果再做正则匹配。 |
| `group` | 正则分组，默认优先取第一个捕获组。 |
| `ignore_case` | 正则是否忽略大小写，默认 `true`。 |
| `replace` | 字符串替换数组，例如 `[["--", "0"]]`。 |
| `type` | 写入字段前的转换类型：`size`、`int`、`float`、`text`、`active`。 |
| `only_empty` | 目标字段已有值时不覆盖。 |
| `continue` | 当前规则成功后继续尝试下一条规则。 |

### json_stats

有些站点把上传量、下载量、分享率等放在页面里的 JSON 中，可以用 `json_stats` 解析。

```json
{
  "userdata": {
    "json_stats": [
      {
        "xpath": "//script[@id='__NEXT_DATA__']/text()",
        "regex": "\"stats\":(\\[.*?\\])",
        "group": 1,
        "mapping": {
          "上传量": "upload",
          "下载量": "download",
          "分享率": "ratio",
          "魔力值": "bonus"
        },
        "label_keys": ["label", "name", "title"],
        "value_key": "value"
      }
    ]
  }
}
```

`json_stats` 支持 JSON 数组，也支持包含 `items`、`data` 或 `stats` 数组的对象。

### calculate_ratio

如果站点没有直接给分享率，但能解析出上传量和下载量，可以自动计算：

```json
{
  "userdata": {
    "calculate_ratio": true,
    "fields": {
      "upload": "//span[@id='uploaded']/text()",
      "download": "//span[@id='downloaded']/text()"
    }
  }
}
```

## 支持字段

常用目标字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `username` | text | 用户名 |
| `user_level` | text | 用户等级 |
| `userid` | text | 用户 ID |
| `upload` | size | 上传量 |
| `download` | size | 下载量 |
| `ratio` | float | 分享率 |
| `bonus` | float | 魔力值、积分、爆米花等 |
| `seeding` | int | 当前做种数 |
| `leeching` | int | 当前下载数 |
| `active` | active | 同时解析做种和下载，格式类似 `↑ 12 / ↓ 3` |
| `seeding_size` | size | 做种体积 |

字段别名会自动归一化：

| 别名 | 目标字段 |
| --- | --- |
| `uploaded`、`上传量` | `upload` |
| `downloaded`、`下载量`、`downloaded_bytes` | `download` |
| `魔力`、`魔力值`、`爆米花`、`karma points` | `bonus` |
| `分享率` | `ratio` |
| `seeders`、`当前做种`、`torrents seeding` | `seeding` |
| `leechers`、`当前下载`、`torrents leeching` | `leeching` |

## base64 生成示例

本地生成一行配置：

```sh
python3 - <<'PY'
import base64
import json

domain = "example.com"
config = {
    "userdata": {
        "calculate_ratio": True,
        "fields": {
            "upload": "//span[@id='uploaded']/text()",
            "download": "//span[@id='downloaded']/text()",
            "bonus": "//span[@id='bonus']/text()",
            "active": "//span[@id='active']/text()"
        }
    }
}

raw = json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
print(f"{domain}|{base64.b64encode(raw).decode()}")
PY
```

把输出结果粘贴到插件配置的“站点适配配置”里即可。

## 调试流程

1. 在浏览器里打开目标站点页面，确认当前账号 Cookie 可以访问。
2. 用开发者工具找到上传量、下载量、分享率等字段所在的 DOM 或 JSON。
3. 写 `userdata.fields` 或 `userdata.json_stats`。
4. 保存插件配置。
5. 到 MoviePilot 站点管理里刷新站点数据。
6. 查看日志中是否出现 `站点适配器索引配置已加载` 或 `站点适配器用户数据解析规则已启用`。

## 日志关键字

排查时可以搜索：

```text
站点适配器
索引配置已加载
索引配置加载失败
用户数据解析规则已启用
用户数据规则执行失败
配置格式错误
```

## 常见问题

### 保存后没有变化

先确认插件已启用，并且“启用用户数据解析规则”已开启。然后重新刷新站点数据。部分站点数据会被 MoviePilot 缓存，必要时重启 MoviePilot。

### 配置格式错误

如果使用 `domain|base64(json)` 格式，确认 base64 解码后是合法 JSON。如果直接粘贴 JSON，确认最外层是对象、数组，或包含 `sites` / `rules` 数组。

### XPath 能在浏览器里查到，但插件解析不到

站点给 MoviePilot 返回的 HTML 可能和浏览器渲染后的 DOM 不一样。优先查看 MoviePilot 请求拿到的原始 HTML，再写 XPath。前端运行后生成的元素通常不能直接用 XPath 解析。

### 用户数据字段显示 0

确认字段类型是否正确。上传量、下载量这类字段建议用 `type: "size"`；分享率和魔力值建议用 `float`；做种数、下载数建议用 `int`。

### 站点规则是否会影响所有站点

不会。插件会按归一化域名匹配，`example.com` 可以匹配 `www.example.com`，但不会影响其他无关域名。

## 注意事项

- 插件只负责配置化接入，不内置具体站点私有规则。
- 站点页面结构变化后，需要更新对应配置。
- 不要把站点 Cookie、账号、密钥写进配置或提交到仓库。
- 如果同时启用其他修改站点解析流程的插件，可能出现覆盖顺序影响。
