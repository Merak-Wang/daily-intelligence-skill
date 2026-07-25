# Daily Intelligence v2.0.0

本次升级在不提高 Agent Token 预算的前提下加入本地新闻监控、来源扩展、事件聚类和多视角分析 2.0。写作批次、图片预取和确定性装配缩短关键路径；日报先交付本地 JSON、Markdown、HTML，再在后台生成 PDF、可选同步 Notion 并安排独立评估。

## 主要功能

- 监控 32 个核心来源与 51 个发现来源；发现来源不增加日报覆盖配额。
- 优先使用 RSS/Atom 和条件请求缓存，必要时回退到静态 HTML 或显式人工验证。
- 零模型 Token 地解析发布时间、图片和来源状态，并进行跨来源事件聚类。
- 提供本机新闻流、事件聚类、来源健康和人工验证情报台。
- 生成中文或英文的 schema 2.0 日报，包括资讯、技术和三类研判。
- 三个视角使用同一事件档案，并给出共识、分歧、传导链、共同观察信号与修正条件。
- 保留来源、证据状态、时间和历史连续性。
- 对登录、限流、挑战和部分失败提供明确状态与恢复路径。
- 使用不可变 Revision 保存 Index、报告和独立 Evaluation。
- 提供响应式 HTML、本地日报索引、A4 PDF 和可续传的 Notion 发布。
- Brief 子任务各自写回受验证的批次结果，主模型只处理最多 18 个候选的紧凑研判包。
- 分别记录 HTML、PDF、Notion 里程碑及模型批次耗时/API/Token，便于下一轮真实运行复盘。

## 兼容性

- Python 3.11、3.12。
- Windows 和 Ubuntu 由 CI 覆盖。
- 继续读取 schema 1.1 至 1.5 报告；新报告使用 schema 2.0。
- 继续读取旧 source-index 的根级和嵌套条目结构。
- `--force` 保留为 `--republish` 的兼容别名。

## 升级说明

- 默认流程使用 `finalize-edition --defer-tail`，HTML 就绪即返回；随后在后台运行 manifest 的 `tail.command` 完成 PDF、Notion 和评估。
- `--publish` 只增加 Notion 同步，不跳过本地保存或报告校验。
- 首次运行会绑定唯一数据根。迁移目录前先执行 `daily-intel data-root status`，再显式执行 `adopt`。
- 人工验证默认不自动打开；使用 `verify-pending` 或显式传入 `--open-verification`。
- 使用 `daily-intel refresh-monitor` 初始化监控快照，再运行 `daily-intel serve --open --refresh-minutes 30` 打开本地情报台。
- 发现来源只负责扩展发现面；既有日报 Token 上限、正文读取上限和核心来源配额保持不变。

安装和使用见 [README](README.md)，完整变更见 [Changelog](CHANGELOG.md)。

## 已知限制

- 网络异常、首次登录或大量验证页面可能超过常规运行时限。
- 项目不会绕过验证码、付费墙、限流或站点访问控制。
- 研判仅用于研究辅助，不构成个性化专业建议。
