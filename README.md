# Daily Intelligence

Daily Intelligence 是面向 [Hermes Agent](https://hermes-agent.nousresearch.com/) 的中文情报日报 Skill。它按晨报和晚报两个时段整理新闻、论文与开源项目，并保留来源链接和历史上下文。

[![Hermes Agent](https://img.shields.io/badge/Hermes-Agent-6C5CE7?style=flat-square)](https://hermes-agent.nousresearch.com/)
[![License](https://img.shields.io/github/license/Merak-Wang/daily-intelligence-skill?style=flat-square)](LICENSE)

## 能做什么

- 从配置的新闻、研究机构、论文和开源社区来源收集候选内容。
- 生成包含资讯、技术和研判的固定结构中文日报。
- 为每条内容保留原文标题、中文摘要、来源和证据边界。
- 生成晨报与晚报；晚报补充日间变化、判断修正和次日观察。
- 在来源失败或需要人工验证时保留状态，继续完成可用部分。
- 保存 JSON、Markdown、HTML 和 PDF；可选同步到 Notion。
- 在交付后生成独立质量评估，并将合格内容用于后续连续跟踪。

日报包含以下栏目：

```text
资讯：国际、国内新闻、军事、市场
技术：技术新闻、论文、开源项目
研判：地缘政治、AI 研究与工程、市场分析
```

## 输出

| 格式 | 用途 |
| --- | --- |
| JSON | 结构化报告和后续处理 |
| Markdown | 审阅与归档 |
| HTML | 本地阅读和日报索引 |
| PDF | 打印与分享 |
| Notion | 可选的远程同步 |

本地文件是事实源；不配置 Notion 也能完成日报生成、评估和归档。

## 快速开始

要求 Python 3.11+、Hermes Agent 和 Hermes Gateway。Windows 使用 Microsoft Edge；macOS 和 Linux 使用 Playwright Chromium。

Windows：

```powershell
git clone https://github.com/Merak-Wang/daily-intelligence-skill.git
cd daily-intelligence-skill
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

macOS / Linux：

```bash
git clone https://github.com/Merak-Wang/daily-intelligence-skill.git
cd daily-intelligence-skill
./scripts/install.sh
```

安装后可直接向 Hermes 发出任务：

```text
使用 daily-intelligence 生成今天的晨报，保存为本地 HTML 和 PDF。
```

```text
使用 daily-intelligence 生成今天的晚报，补充日间变化和次日观察。
```

只有明确要求发布到 Notion 时才会访问 Notion。

## 配置与文档

- 来源和输出配置：[configs/sources.yaml](configs/sources.yaml)
- 日常运行与故障恢复：[references/runbook.md](references/runbook.md)
- Windows 安装：[references/windows-setup.md](references/windows-setup.md)
- Notion 配置：[references/notion-setup.md](references/notion-setup.md)
- 编辑与证据规则：[references/editorial-policy.md](references/editorial-policy.md)
- 开发文档：[wiki/Home.md](wiki/Home.md)
- 版本记录：[CHANGELOG.md](CHANGELOG.md)

## 开发

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest
python -m compileall -q src tests
```

修改来源过滤、状态、校验、发布或旧格式兼容逻辑时，需要同步更新测试。开发约定和模块索引见 [Wiki](wiki/Home.md)。

## 使用边界

- 项目不会绕过登录、验证码、限流、付费墙或站点访问控制。
- 访问失败的来源会保留为失败或待验证状态，不会被记为“无内容”。
- 研判仅用于研究辅助，不构成投资、法律或其他专业建议。

## License

[MIT](LICENSE) © Wang Mingfeng
