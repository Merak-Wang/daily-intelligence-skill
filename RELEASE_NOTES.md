# Daily Intelligence v1.0.0

首个稳定版本：把新闻、论文和开源动态组织成一份证据可追溯、历史可连续、失败可恢复的中文情报日报。

## 亮点

- 06:00 晨报与 18:00 晚报双时段工作流；晚报在晨报基础上补充新增、确认、修正和次日观察。
- “资讯、技术、研判”固定结构，来源内最多 15 条，保留原始 TopN 并按编辑重要性重排。
- Hermes 负责翻译、TL;DR 与三视角研判；Python 负责 ID、引用、状态、Schema、revision 和发布等确定性机制。
- 通用页面并发 HTTP 预取，登录/JavaScript/人工验证页面使用持久化 Edge profile；正文按重要性少量并行读取。
- 本地 JSON/Markdown 为事实源，默认生成响应式 HTML、本地归档首页和 A4 PDF；Notion 改为显式可选投影。
- 独立评估 Agent 在日报交付后异步执行，按九个固定维度评分，并将合格内容纳入后续连续性。
- 保留 schema 1.1—1.4 和旧 source-index 双层结构的读取兼容性。

## 安装

Windows：

```powershell
git clone https://github.com/Merak-Wang/daily-intelligence-skill.git
cd daily-intelligence-skill
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
hermes skills list
```

macOS / Linux：

```bash
git clone https://github.com/Merak-Wang/daily-intelligence-skill.git
cd daily-intelligence-skill
./scripts/install.sh
hermes skills list
```

## 升级说明

- Windows 原生 Hermes Home 使用 `%LOCALAPPDATA%\hermes`；Linux/macOS 默认使用 `~/.hermes`。
- `finalize-edition` 不带 `--publish` 也会完成 JSON/Markdown/HTML/PDF 本地交付并调度独立评估。
- `--publish` 仅表示在本地交付之外同步到 Notion，不会绕过报告验证。
- 第一次运行会绑定唯一数据根；如果迁移过运行目录，请先执行 `daily-intel data-root status`，确认后再显式 `adopt`。

## 验证

- 134 项 pytest 测试通过。
- Ruff、compileall、PowerShell 安装器解析、PortableGit Bash 语法与 README 本地链接检查通过。
- Windows Edge 已验证响应式 HTML 与六页中文 A4 PDF 输出。
- Wheel：`daily_intelligence_skill-1.0.0-py3-none-any.whl`
- SHA-256：`d205d7890311626be10a1d5a3394f67f8e8c60960ccdbf47fec11c3d1af5cc40`

## 已知边界

- 10 分钟是正常网络与既有登录状态下的运行目标，不是大面积站点验证或首次配置时的硬实时承诺。
- 系统不会绕过 CAPTCHA、临时限流、付费墙或站点访问控制；失败来源会保留有效链接并允许生成 partial 报告。
- 市场研判仅用于研究辅助，不执行交易，也不替代个性化专业意见。

完整说明见 [README](README.md)、[中文 Wiki](wiki/Home.md) 与 [Changelog](CHANGELOG.md)。
