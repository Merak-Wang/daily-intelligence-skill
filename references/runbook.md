# 日报运行手册

## 调度与预算

```text
05:50 候选就绪 -> 两层选择/按需正文 -> 生成 -> 事实校验 -> 06:00 发布
发布后每 2 分钟、最多 3 次 独立评估 -> 保存评估 artifact -> 追加同一 Notion 页面
17:50 候选就绪 -> 两层选择/按需正文 -> 修正 -> 事实校验 -> 18:00 发布
发布后每 2 分钟、最多 3 次 独立评估 -> 保存评估 artifact -> 追加同一 Notion 页面
```

每次正常运行最多 600 秒，Agent 输入加输出最多 10,000,000 token。采集脚本继续处理单源错误；Agent 优先处理高分候选和影响研判的正文。开发/调试不受该预算限制。

生成与评估保持角色隔离。生成任务调用 `finalize-edition --publish` 后，由 Python 创建最多执行 3 次的 Hermes Cron，以容忍临时模型/API 连接失败；首次成功后后续执行检查到 completed 即退出。评估任务只读已保存报告/索引和契约，禁止修改报告，并调用 `finalize-evaluation --publish`。Gateway 必须运行并在 Windows 登录时自启动；调度失败只写入 run，不撤回日报。晚间生成读取当天晨报和已存在的晨报评估；晨报评估尚未完成时按未评估历史处理。

## 交互式验证

无人值守采集发现挑战只记录状态，不弹窗。交互式 Hermes Desktop 应运行 `run-edition --open-verification --verification-timeout-seconds 180`：采集结束存在失败、待验证或限流页面时自动打开小前端；没有待处理页面时不启动 Edge。若采集时未启用或稍后需要重开，再运行 `verify-pending`。Windows 使用可见 Edge 和专用 profile；两种入口复用同一实现，只打开队列页，不预先打开所有失败网站。由 CLI 启动时页面显示“采集器已连接”，并实时更新每条链接的等待、验证、采集和失败状态；直接双击静态 HTML 时显示“未连接”，不会假装正在采集。

- 用户点击链接且页面验证成功：立即从当前页面提取，并原子合并进新索引。
- 页面关闭、403、超时或提取失败：立即跳过，保留链接和失败状态。
- 页面显示 temporarily limited/restricted 或返回 429：标为 `rate_limited`，停止本轮自动重试，等待后续时段；不得反复刷新或尝试绕过。
- 部分成功：继续日报，不要求所有来源成功。
- 验证后无需 `resume`；若已有日报，当前 Hermes 任务继续生成补充修订并发布。

定时任务、Gateway 和无人值守会话不得传 `--open-verification`，也不得等待人工验证。

## 恢复

- `awaiting_selection`：选择 ID，运行 `enrich-edition`。
- `awaiting_authoring`：生成 schema 1.5 草稿并运行 `finalize-edition`；不要同步等待评估。
- `finalizing` 本地失败：状态自动退回 `awaiting_authoring` 并记录错误。
- `publishing` 失败：重试 `finalize-edition --publish`。
- `failed`：阅读 manifest 的 `error`，修复后用 `run-edition --restart`。
- `completed_partial`：本地报告有效；待验证链接可留到后续处理。
- `evaluation pending`：日报已经完成；由独立评估调度读取 run 中的 report ID/content hash。
- 评估失败：保留 pending/错误日志，稍后重跑 `finalize-evaluation`；不得撤回日报或由生成 Agent 自评。

run manifest 固定在 `DATA_DIR/runs/YYYY-MM-DD/<edition>.json`。不要手改状态文件。删除过期锁前必须确认没有活动进程。

## 验收

发布检查：run/index/report 一致、七个 section、每源目标/上限、brief/精选事件关系、发布时间与 NEW、URL/标题身份、正文访问等级、研判精选事件引用、JSON/Markdown、待验证链接及可选 Notion page ID。

评估检查：九维完整、总分正确、被评 report ID/hash 匹配、独立 artifact 存在、Notion 已追加或可重试、长期连续状态按建议更新。

运行复盘中的计数只能来自 manifest 和根级 `items[]`；不得把 `verification_required`、`failed` 或 `metadata_only` 说成 `no_items`。
