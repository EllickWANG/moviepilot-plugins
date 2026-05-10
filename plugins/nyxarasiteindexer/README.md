# Nyxara站点索引配置

本插件复刻 `自定义索引站点` 的索引配置能力，并额外提供可配置的站点用户数据解析规则。

插件本体不写死站点规则。HDCity、观众等站点的资源列表和用户数据修复，都通过插件配置下发。

## 索引站点配置

兼容 `自定义索引站点`：

```text
域名|配置 json 的 base64 编码
```

## 用户数据解析配置

同样使用一行一个站点：

```text
域名|规则 json 的 base64 编码
```

规则支持：

- `fields`：按字段配置 `xpath`、`regex`、`attribute`、`type`。
- `json_stats`：从页面属性中读取 JSON 统计数据，并按 `mapping` 写入字段。
- `calculate_ratio`：当页面没有分享率且下载量非 0 时，根据上传/下载计算。

常用字段包括 `username`、`user_level`、`userid`、`upload`、`download`、`ratio`、`bonus`、`seeding`、`leeching`、`active`。

插件不修改 MoviePilot 核心源码。
