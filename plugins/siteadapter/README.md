# 站点适配器

本插件提供配置化的站点索引与用户数据解析适配。

每个站点使用一段配置，同时描述资源列表规则和用户数据规则：

```text
域名|配置 json 的 base64 编码
```

配置 JSON 示例：

```json
{
  "indexer": {
    "schema": "NexusPhp",
    "search": {},
    "torrents": {}
  },
  "userdata": {
    "fields": {
      "upload": {
        "xpath": "//i[starts-with(@title,\"Uploaded\")]/following-sibling::text()[1]"
      }
    }
  }
}
```

说明：

- `indexer` 兼容原 `自定义索引站点` 的站点规则。
- `userdata` 支持 `fields`、`json_stats`、`xpath`、`regex`、`attribute`、`type`。
- 常用用户数据字段包括 `username`、`user_level`、`userid`、`upload`、`download`、`ratio`、`bonus`、`seeding`、`leeching`、`active`。

插件不修改 MoviePilot 核心源码。
