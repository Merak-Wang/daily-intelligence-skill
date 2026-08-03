# 日报运行手册

**状态：** 已验证运行参考
**最后对照代码：** 2026-08-02
**上级地图：** [`ARCHITECTURE.md`](../ARCHITECTURE.md)

## 调度与预算

```text
05:50 候选就绪 -> brief 批次/图片预取并行 -> 紧凑研判 -> 事实校验 -> 06:00 本地 HTML
HTML 交付后 -> 后台 PDF/可选 Notion -> 每 2 分钟、最多 3 次独立评估 -> 刷新 HTML/PDF
17:50 候选就绪 -> brief 批次/图片预取并行 -> 紧凑研判 -> 事实校验 -> 18:00 本地 HTML
HTML 交付后 -> 后台 PDF/可选 Notion -> 每 2 分钟、最多 3 次独立评估 -> 刷新 HTML/PDF
```

每次正常运行最多 600 秒，Agent 输入加输出最多 10,000,000 token。采集脚本继续处理单源错误；Agent 优先处理高分候选和影响研判的正文。开发/调试不受该预算限制。

监控层独立于 Agent 预算。按需运行 `daily-intel refresh-monitor`，或用 `daily-intel serve --open --refresh-minutes 30` 在本地情报台后台刷新；RSS/Atom、静态 HTML、时间解析、图片元数据、聚类和来源健康均不调用模型。51 个发现来源的 `report_target` 和 `report_max` 均为 0，只扩展发现面，不扩大日报篇幅、正文读取上限或研判任务。`run-edition` 优先复用默认 90 分钟内的新鲜零-token 快照；仅在快照缺失或过期时刷新，刷新失败则保留旧快照并继续原采集流程。可用 `monitor.snapshot_max_age_minutes` 和 `monitor.reuse_fresh_snapshot_before_edition` 调整这一策略。

生成与评估保持角色隔离。`finalize-edition --defer-tail` 在不可变 JSON/Markdown 和 HTML 就绪后返回；主任务立即交付 `artifacts.html_path`，再把 run 中的 `tail.command` 放进启用完成通知的后台 terminal。`complete-edition-tail` 生成 PDF、按请求发布 Notion，并由 Python 创建最多执行 3 次的 Hermes Cron，以容忍临时模型/API 连接失败；首次成功后后续执行检查到 completed 即退出。评估任务只读已保存报告/索引和契约，禁止修改报告，并调用 `finalize-evaluation`。目标日报显式使用了 `--publish` 时，调度提示才给评估命令追加 `--publish`。Gateway 必须运行并在 Windows 登录时自启动；tail 或调度失败只写入 run，不撤回本地日报。晚间生成读取当天晨报和已存在的晨报评估；晨报评估尚未完成时按未评估历史处理。

## 交互式验证

`run-edition` 默认只记录失败、待验证或限流页面，不启动 Edge，也不等待人工操作。用户准备好交互时运行 `verify-pending`；显式 `run-edition --open-verification` 复用同一实现，但会等待队列完成或超时。Windows 使用可见 Edge 和专用 profile；验证入口只打开队列页，不预先打开所有失败网站。由 CLI 启动时页面显示“采集器已连接”，并实时更新每条链接的等待、验证、采集和失败状态；直接双击静态 HTML 时显示“未连接”，不会假装正在采集。

- 用户点击链接且页面验证成功：立即从当前页面提取，并原子合并进新索引。
- 页面关闭、403、超时或提取失败：立即跳过，保留链接和失败状态。
- 页面显示 temporarily limited/restricted 或返回 429：标为 `rate_limited`，停止本轮自动重试，等待后续时段；不得反复刷新或尝试绕过。
- 部分成功：继续日报，不要求所有来源成功。
- 验证后无需 `resume`；若已有日报，当前 Hermes 任务继续生成补充修订并发布。

定时任务、Gateway 和无人值守会话不得传 `--open-verification`。`--unattended` 保留为默认非交互行为的兼容参数。

## 数据根与耗时诊断

所有命令必须使用同一个 `DATA_DIR`。`data-root status` 显示当前绑定；只有确认迁移时才执行 `data-root adopt`。run 内的 `data_root` 与 artifact 路径必须一致，跨根引用在读取前失败。run 的 `artifacts.collection_metrics` 记录来源、候选、状态分布以及监控刷新/复用；`artifacts.enrichment.pipeline` 记录正文缓存、HTTP 提取、Edge 回退及各阶段耗时；报告保存结果的 `save_metrics` 区分编译校验、媒体、持久化、本地投影和状态更新。

`artifacts.authoring.metrics` 记录每个 brief 批次的 duration、API、输入/输出 token、模型、退出原因，以及 brief 合并、图片预取、紧凑研判与总写作时间；Hermes 当前未提供的 queue/首 token/prefill/decode 字段保持空值，不能估算。`milestones.local_html_ready_at`、`pdf_ready_at`、`notion_ready_at` 分开记录读者可见时间；`metrics.tail_seconds` 不混入 HTML 关键路径。`metrics.phase_durations_seconds` 汇总采集、context、正文、Agent 写作等待和验证/定稿，用这些机器计时定位慢点，不再用人工估算或一个总耗时混合后台工作。

## 恢复

- `awaiting_selection`：选择 ID，运行 `enrich-edition`。
- `awaiting_authoring` 且没有 session：运行 `begin-authoring`，一次后台 `delegate_task` 分发所有 packet，同时运行 `prefetch-media`。
- authoring 批次已返回：把 delegation JSON 写入 session 指定路径并运行 `record-authoring-metrics`；`authoring-status` 全部完成后运行 `prepare-analysis`。只有输出 `deadline_exceeded: true` 时才能用 `prepare-analysis --allow-degraded`。
- `analysis_pending`：主 Agent 只读 `analysis_packet_path`，写 `analysis_result_path`，运行 `assemble-authoring`、`validate-report --run` 和 `finalize-edition --defer-tail`。
- `finalizing` 在事实源持久化前失败：状态自动退回 `awaiting_authoring` 并记录错误；HTML/PDF 投影失败时 JSON/Markdown 仍有效，结果会记录 `local_output_error`，修复环境后重建投影即可，不要重新生成 revision。
- `tail.pending`：HTML 已有效；在后台执行 manifest 的精确 `tail.command`。
- `tail.partial`：读取 `tail.errors`，修复后重跑 `complete-edition-tail`；已存在 PDF/Notion 不会重复创建。
- 旧同步路径的 `publishing` 失败：重试 `finalize-edition --publish`。
- `failed`：阅读 manifest 的 `error`，修复后用 `run-edition --restart`。
- `completed_partial`：本地报告有效；待验证链接可留到后续处理。
- `evaluation pending`：日报已经完成；由独立评估调度读取 run 中的 report ID/content hash。
- 评估失败：保留 pending/错误日志，稍后重跑 `finalize-evaluation`；不得撤回日报或由生成 Agent 自评。
- 监控部分失败：运行 `monitor-status` 查看来源状态；失败、限流和待验证不得改写为 `no_items`。旧快照仍可读取，下一次刷新会按 ETag/Last-Modified 和退避状态重试。

run manifest 固定在 `DATA_DIR/runs/YYYY-MM-DD/<edition>.json`。不要手改状态文件。删除过期锁前必须确认没有活动进程。

## 验收

前台交付检查：run/index/report 一致、schema 2.0、七个 section、每源目标/上限、brief/精选事件关系、发布时间（缺失时显示采集时间）与 NEW、URL/标题身份、正文访问等级、三个视角使用同一事件档案、跨视角综合、JSON/Markdown/HTML、`reports/index.html`、`local_html_ready_at` 和待验证链接。浏览器验收还要确认每条 brief 的标题先于配图、版本化 HTML 的相对图片存在，以及桌面 HTML 单独移入无媒体目录后所有内嵌图片均可加载。

后台收尾检查：tail 为 `completed` 或有可操作的 `partial` 错误；PDF、`pdf_ready_at`、可选 Notion page ID/`notion_ready_at` 和独立评估调度彼此可重试，不影响前台 HTML 有效性。PDF 必须在断开本地媒体目录与网络后仍能显示全部已物化图片；用渲染抽查和 PDF image XObject 计数确认图片已写入文件，而不是保留外链。

评估检查：九维完整、总分正确、被评 report ID/hash 匹配、独立 artifact 存在、HTML/PDF 已刷新、可选 Notion 已附加更新版 HTML 或可重试、长期连续状态按建议更新。

运行复盘中的计数只能来自 manifest 和根级 `items[]`；不得把 `verification_required`、`failed` 或 `metadata_only` 说成 `no_items`。

监控检查：`snapshot.json` 的 `token_usage` 为 0、来源总数与配置一致、story ID 跨刷新稳定、缺失发布时间的条目明确回退到采集时间、`health.json` 保留每个失败原因。本地情报台默认只绑定 `127.0.0.1`。
