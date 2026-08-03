# 迹简情报台 · SignalTrail

[简体中文](README.md) | [English](README.en.md)

> 信号化繁为简，来源有迹可循。

迹简情报台（SignalTrail）是运行在 [Hermes Agent](https://hermes-agent.nousresearch.com/) 中的
本地优先情报简报 Skill。它从经批准的公开来源收集更新，聚合同一事件的重复报道，
再协助 Hermes 生成中文或英文成品简报。报告保留证据链、来源健康状态与跨期连续性，
把分散信号整理成可复核、可汇报、可持续跟踪的判断路径，适合日常研究、产品汇报和团队晨会。

[报告展厅](#报告展厅) · [快速开始](#快速开始) ·
[工程文档](docs/zh-CN/README.md) ·
[Hermes Skill](SKILL.md)

[![Hermes Agent](https://img.shields.io/badge/Hermes-Agent-6C5CE7?style=flat-square)](https://hermes-agent.nousresearch.com/)
[![License](https://img.shields.io/github/license/Merak-Wang/signaltrail-skill?style=flat-square)](LICENSE)

![迹简情报台报告预览](https://raw.githubusercontent.com/Merak-Wang/signaltrail-skill/main/assets/readme/morning-report-preview.png)

## 产品交付

迹简情报台把庞杂的每日阅读队列压缩为结构稳定、可以复核和归档的决策材料：

- **可直接阅读的晨报与晚报**：HTML 自动交付，PDF 便于汇报与分享，Markdown 和
  JSON 作为可持续维护的本地记录。
- **可追溯的信息覆盖**：保留原题、来源、链接、发布时间及访问限制，不把来源过程
  隐藏在不可核对的摘要后面。
- **七个固定栏目**：覆盖国际、国内、军事、市场、技术、论文与开源项目。
- **三个分析视角与综合研判**：从地缘政治、AI/技术、市场分别展开，并总结共同信号、
  分歧与下一步观察指标。
- **零模型 Token 的本地监控层**：信息流刷新、缓存、去重、聚类和来源健康检查由
  Python 在本机完成；模型只在正式报告的筛选、目标语言写作与研判阶段参与。
- **默认本地所有权**：版本化文件、可移动的桌面单文件 HTML，以及按需启用的
  Notion 交付。

内置配置把 32 个正式日报来源与 51 个发现来源分开管理。正式来源控制报告篇幅，
发现来源扩大信号面，但不会悄悄增加编辑量或模型预算。

## 报告展厅

以下存档展示产品在实际信息规模下的阅读体验：把数百条更新压缩为十个可复核的重点
事件，同时保留来源链接与多领域研判。

| 版本 | 当次覆盖 | 编辑结果 | 决策主题 |
| --- | ---: | ---: | --- |
| [2026-07-24 晨报 r3](https://github.com/Merak-Wang/signaltrail-skill/blob/main/examples/reports/2026-07-24-morning-r3.html) | 24 个来源 · 197 条更新 | 10 个重点事件 | 能源、关税与 AI 资本效率 |
| [2026-07-25 晨报 r1](https://github.com/Merak-Wang/signaltrail-skill/blob/main/examples/reports/2026-07-25-morning-r1.html) | 29 个来源 · 235 条更新 | 10 个重点事件 | 能源通道、科技监管与智能体工程 |

这两份是保留的 schema 1.5 历史版本，因此仍使用早期 **Daily Intelligence** 抬头。
当前 schema 2.0 已增加必需的跨视角综合，并统一使用 **迹简情报台 · SignalTrail** 品牌。GitHub
可能直接显示 HTML 源码；下载文件后用浏览器打开即可查看完整报告。

测试样例与报告展厅说明见
[examples/README.md](https://github.com/Merak-Wang/signaltrail-skill/blob/main/examples/README.md)。

## 适用场景

迹简情报台面向已经使用 Hermes、希望在本机生成固定结构中英文简报的个人研究者与
小团队。当“为什么选这条”“来源是否读取成功”与摘要本身同样重要时，它尤其合适。

它不是企业级情报平台，不包含付费研报库、全网社交媒体数据、移动客户端、SSO、
RBAC 或 SLA，也不会绕过登录、验证码、付费墙、限流或其他访问控制。

| 需求 | 迹简情报台的交付方式 |
| --- | --- |
| 每日管理层汇报 | 固定晨报/晚报结构与有界的重点选择 |
| 证据复核 | 原始来源、原题、链接、时间和状态保持可见 |
| 持续态势感知 | 本地信息台、事件聚类、来源健康与人工验证队列 |
| 中英文交付 | 内容、界面、PDF 与 Markdown 使用同一目标语言 |
| 长期归档 | JSON/Markdown 为本地事实源，HTML/PDF 可重建 |
| 远程协作 | 可选 Notion 元数据页面与便携 HTML 附件 |

## 快速开始

### 环境要求

- Git 与 Python 3.11 或更高版本
- 已配置 [Hermes Agent](https://hermes-agent.nousresearch.com/) 与 Hermes Gateway
- Windows 使用系统 Microsoft Edge；macOS 和 Linux 安装脚本会准备 Playwright
  Chromium

### 从 GitHub 安装

Windows：

```powershell
git clone https://github.com/Merak-Wang/signaltrail-skill.git
cd signaltrail-skill
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

macOS / Linux：

```bash
git clone https://github.com/Merak-Wang/signaltrail-skill.git
cd signaltrail-skill
bash ./scripts/install.sh
```

全新 Ubuntu 或 Debian 机器还需安装 Chromium 系统依赖：

```bash
python -m playwright install-deps chromium
```

安装脚本会把 Skill 同步到 `skills/research/signaltrail`，并继续提供向后兼容的
`daily-intel` 命令：

```text
daily-intel --help
```

升级不会重命名或拆分现有 `$HERMES_HOME/daily-intelligence` 数据根；资源查找也继续
支持旧的 `skills/research/merak-brief` 与 `skills/research/daily-intelligence` 目录。
确认新的 `/signaltrail` 正常后，如果 Hermes 同时显示旧品牌条目，可以再移除旧 Skill 目录。

### 生成第一份报告

在 Hermes 中发送：

```text
使用迹简情报台（SignalTrail）生成今天的中文晨报，保存为本地 HTML 和 PDF。
```

或者：

```text
使用 SignalTrail 生成英文晚报，说明今天发生了哪些变化，并给出明日观察信号。
```

默认输出语言是 `zh-CN`。单次生成英文报告：

```text
daily-intel run-edition --edition morning --language en
```

长期默认值写在 `configs/sources.yaml`：

```yaml
output:
  language: zh-CN  # zh-CN 或 en
```

选定语言后，摘要、研判、栏目名、HTML、PDF 和 Markdown 会使用同一种语言。新闻原题
保持不变，只在需要时补充译题。

## 工作流程

```mermaid
flowchart LR
    A["采集经批准的公开来源"] --> B["规范化、去重并聚合同一事件"]
    B --> C["生成有界证据包并选择重点"]
    C --> D["用中文或英文编写简报"]
    D --> E["校验 Schema、引用、状态与语言"]
    E --> F["立即交付桌面 HTML"]
    F --> G["在可重试尾部完成 PDF、可选 Notion 与独立评估"]
```

单个来源失败不会中止其他来源。失败、限流或待人工验证会保留真实状态和恢复路径，
不会被静默改写为 `no_items`。

## 本地情报台

刷新并检查信息流：

```text
daily-intel refresh-monitor
daily-intel monitor-status
```

打开本地信息台，并在进程运行期间每 30 分钟刷新：

```text
daily-intel serve --open --refresh-minutes 30
```

信息台默认只监听 `127.0.0.1`。安装不会注册系统服务；无人值守刷新需要保持该进程
运行，或配置操作系统计划任务。

来源组合可以直接修改：

- 正式日报来源：[configs/sources.yaml](configs/sources.yaml)
- 发现来源：[configs/discovery-sources.yaml](configs/discovery-sources.yaml)

## 输出约定

| 产物 | 用途 |
| --- | --- |
| `reports/YYYY-MM-DD/EDITION-rN.json` | 版本化结构记录，不覆盖已有修订 |
| `reports/YYYY-MM-DD/EDITION-rN.md` | 便于审阅、比较与归档 |
| `reports/YYYY-MM-DD/EDITION-rN.html` | 完整本地阅读版本 |
| `reports/YYYY-MM-DD/EDITION-rN.pdf` | 打印与分享版本，校验后的图片写入文件 |
| `reports/index.html` | 按日期与修订浏览本地历史 |
| `Desktop/daily-intelligence-…html` | 图片内嵌、可独立移动的单文件副本 |
| Notion | 可选的元数据页面与便携 HTML 附件 |

默认报告历史位于 Windows 的
`%LOCALAPPDATA%\hermes\daily-intelligence\reports`，或 macOS/Linux 的
`~/.hermes/daily-intelligence/reports`。设置 `HERMES_HOME` 后，仍在其中使用
`daily-intelligence/reports`，保证既有数据连续。

Notion 完全可选；不配置凭据也能获得全部本地文件。如果桌面复制、PDF 或 Notion
交付失败，版本化本地记录仍会保留，相应投影可以单独重试。

## Hermes 社区发布包

`SKILL.md` 已按 Hermes/Agent Skills 结构组织：公共元数据位于 frontmatter 根级，
Hermes 的发现与配置放在 `metadata.hermes`，详细策略通过 `references/`、
`templates/` 和确定性脚本按需加载。

从 Git 已跟踪文件的白名单构建发布目录：

```text
python scripts/build_hermes_skill.py
```

该命令会检查元数据、目录名称、运行所需文件、禁止的运行时路径、文件大小与常见
密钥模式，并输出 `dist/signaltrail`。发布这个目录，而不是整个仓库：

```text
hermes skills publish ABSOLUTE_PATH/dist/signaltrail --to github --repo OWNER/REPOSITORY
```

使用绝对路径可以避免和 Hermes 本地 Skill 根目录混淆。创建社区 PR 前，应复核
`git status`、生成文件清单和 Hermes 安全扫描结果。

## 文档

- 仓库地图：[AGENTS.md](docs/zh-CN/AGENTS.md)
- 顶层架构：[ARCHITECTURE.md](docs/zh-CN/ARCHITECTURE.md)
- 工程记录目录：[docs/zh-CN/README.md](docs/zh-CN/README.md)
- Skill 执行流程：[SKILL.md](SKILL.md)
- 日常运行与恢复：[references/runbook.md](references/runbook.md)
- 编辑与证据规则：[references/editorial-policy.md](references/editorial-policy.md)
- 详细架构与状态模型：[references/system-design.md](references/system-design.md)
- Windows 安装：[references/windows-setup.md](references/windows-setup.md)
- Notion 配置：[references/notion-setup.md](references/notion-setup.md)
- 当前质量评分：[docs/zh-CN/quality-score.md](docs/zh-CN/quality-score.md)
- 版本记录：[CHANGELOG.md](CHANGELOG.md)

## 开发与验证

```text
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest
python -m compileall -q src tests scripts
python scripts/check_code_comments.py
python scripts/check_docs.py
```

修改来源过滤、状态模型、校验或发布逻辑时必须同步更新测试。运行时 `data/`、浏览器
profile、Cookie、账号截图、认证 HTML 和密钥不得进入仓库。

## License

[MIT](LICENSE) © Wang Mingfeng
