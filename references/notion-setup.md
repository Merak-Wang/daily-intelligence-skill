# Notion 配置

Notion 是可选输出。未设置凭证时，日报仍会保存为本地 JSON、Markdown、HTML 和 PDF。

## 创建 Connection 并获取 Token

1. 在 Notion 中进入 **Settings** → **Connections** → **Develop or manage Connections**。
2. 在 Developer Portal 的 **Internal connections** 中创建 Connection。
3. 打开该 Connection 的 **Configuration** 页面，复制 **Installation access token**。
4. 为 Connection 启用读取、插入和更新内容权限。

新 Token 通常以 `ntn_` 开头。Token 只应存放在环境变量或密钥管理器中，不要写入仓库、日志或报告。

## 授权目标数据库

新建 Connection 默认不能访问任何页面。打开目标数据库，点击右上角 **•••** → **Connections** → **Add connection**，选择刚创建的 Connection。

也可以在 Developer Portal 的 **Content access** 页面授权目标数据库。

## 获取正确的 ID

本项目需要的是 `NOTION_DATA_SOURCE_ID`，不是数据库视图 URL 中的 `view_id`。

当前 Notion API 区分：

- `database_id`：数据库容器 ID；
- `data_source_id`：数据库内具体数据源的 ID，本项目使用这个值。

推荐方式：打开数据库完整页面，点击右上角 **•••** → **Manage data sources** → **Copy data source ID**。

如果只能复制数据库链接：

1. 点击 **Share** 或右上角 **•••** → **Copy link**。
2. 链接通常类似：

   ```text
   https://www.notion.so/WorkspaceName/DatabaseName-12345678abcd1234abcd1234abcd1234?v=...
   https://www.notion.so/12345678abcd1234abcd1234abcd1234?v=...
   ```

3. URL 中问号前的 32 位十六进制字符串是 `database_id`。API 接受带连字符或不带连字符的 UUID：

   ```text
   12345678-abcd-1234-abcd-1234abcd1234
   12345678abcd1234abcd1234abcd1234
   ```

4. `database_id` 不一定等于 `data_source_id`。应通过 **Manage data sources** 复制 Data Source ID，或调用 Retrieve a database API，从响应的 `data_sources` 数组读取 ID。

对于形如 `/ds/{database_uuid}/{data_source_uuid}` 的链接，第二个 UUID 才是 `data_source_id`。把第一个 UUID 传给 `/v1/data_sources/{id}` 通常会返回 404。

官方说明：

- [创建 Internal Connection](https://developers.notion.com/guides/get-started/internal-connections)
- [查找 Database 与 Data Source ID](https://developers.notion.com/guides/data-apis/working-with-databases)

## 设置环境变量

PowerShell：

```powershell
$env:NOTION_TOKEN = "ntn_..."
$env:NOTION_DATA_SOURCE_ID = "..."
```

Bash：

```bash
export NOTION_TOKEN="ntn_..."
export NOTION_DATA_SOURCE_ID="..."
```

Hermes 运行时也会读取 Hermes Home 下的 `.env`。仓库内的 `.env` 已被 Git 忽略。

## 数据库字段

发布器会读取实际 schema，再选择兼容配置。字段不匹配时会报错，不会自动修改共享数据库。

`hermes_notes` 配置支持：

| 字段 | 类型 | 写入值 |
| --- | --- | --- |
| Name | Title | 报告标题 |
| Date | Date | 报告日期 |
| Status | Status | 晨报为 `New`，晚报为 `Reviewed` |
| Source | Select | `Daily Intelligence` |
| Tags | Multi-select | 项目名和版本时段 |

`daily_intelligence` 配置还支持 `Version`、`Source Count`、`Event Count` 和 `Pending Verification`。旧版顶层 `properties`、`values` 配置仍可读取。

## 发布行为

- 晨报创建或复用当日页面；晚报复用同一日期页面并更新属性。
- 页面正文不再复制新闻、研判或证据字段。每个时段只上传一份由已校验 Report 确定性生成的便携 HTML，并以 `file_upload` 文件块附在页面中。
- 便携 HTML 不引用本机绝对路径；配图使用报告中已经校验的公开来源 URL。下载附件后即可独立打开，阅读层以 HTML 为准。
- 本地发布登记保存页面 ID、HTML SHA-256、File Upload ID 和追加进度，用于断点续传和避免重复附件。
- 独立评估完成后，如显式要求同步 Notion，则再附加一份包含评估结果的更新版 HTML，不发布评估富文本块。
- 新的 HTML 附件模式不需要逐图上传或图片回填。`backfill-notion-images` 只兼容旧版富文本页面；对 HTML 附件页面返回 `not_applicable_html_attachment`。
- `--republish` 只用于明确的重新发布，不会跳过报告或 Notion schema 校验。
- Notion 失败不会破坏已经保存的本地报告。

只上传系统生成的日报 HTML，不会上传抓取到的网页原始 HTML、正文缓存、Cookie、浏览器 Profile、付费内容或内部模型指令。

Notion 官方说明：[上传小文件](https://developers.notion.com/guides/data-apis/uploading-small-files)、[File 块与 File Upload](https://developers.notion.com/reference/block)。
