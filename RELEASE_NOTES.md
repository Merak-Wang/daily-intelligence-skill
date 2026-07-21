# Daily Intelligence v1.0.0

首个稳定版本提供面向 Hermes Agent 的晨报、晚报工作流，默认生成本地 JSON、Markdown、HTML 和 PDF，可选同步到 Notion。

## 主要功能

- 收集新闻、研究机构、论文与开源社区来源。
- 生成资讯、技术和三类研判组成的中文日报。
- 保留来源、证据状态、时间和历史连续性。
- 对登录、限流、挑战和部分失败提供明确状态与恢复路径。
- 使用不可变 Revision 保存 Index、报告和独立 Evaluation。
- 提供响应式 HTML、本地日报索引、A4 PDF 和可续传的 Notion 发布。

## 兼容性

- Python 3.11、3.12。
- Windows 和 Ubuntu 由 CI 覆盖。
- 继续读取 schema 1.1 至 1.4 报告。
- 继续读取旧 source-index 的根级和嵌套条目结构。
- `--force` 保留为 `--republish` 的兼容别名。

## 升级说明

- `finalize-edition` 不带 `--publish` 也会完成全部本地交付。
- `--publish` 只增加 Notion 同步，不跳过本地保存或报告校验。
- 首次运行会绑定唯一数据根。迁移目录前先执行 `daily-intel data-root status`，再显式执行 `adopt`。
- 人工验证默认不自动打开；使用 `verify-pending` 或显式传入 `--open-verification`。

安装和使用见 [README](README.md)，完整变更见 [Changelog](CHANGELOG.md)。

## 已知限制

- 网络异常、首次登录或大量验证页面可能超过常规运行时限。
- 项目不会绕过验证码、付费墙、限流或站点访问控制。
- 研判仅用于研究辅助，不构成个性化专业建议。
