# Daily Intelligence

Daily Intelligence 是面向 [Hermes Agent](https://hermes-agent.nousresearch.com/) 的中文情报日报 Skill。它在本地持续监控新闻、论文与开源项目，在晨报和晚报中完成广覆盖摘要、重点事件筛选、三视角研判与跨视角综合，并交付可追溯的 JSON、Markdown、HTML 和 PDF。

[![Hermes Agent](https://img.shields.io/badge/Hermes-Agent-6C5CE7?style=flat-square)](https://hermes-agent.nousresearch.com/)
[![License](https://img.shields.io/github/license/Merak-Wang/daily-intelligence-skill?style=flat-square)](LICENSE)

## 这套系统解决什么问题

- **更大的发现面，不增加监控 Token**：32 个核心来源承担日报覆盖，51 个发现来源扩展新闻流；RSS/Atom、条件请求、静态 HTML、图片和聚类都由本地 Python 完成。
- **宽覆盖与深研判分层**：`briefs[]` 保留来源级覆盖，最多 12 个精选事件进入完整证据链和连续跟踪，主模型只读取最多 18 个候选的紧凑分析包。
- **同一事实，不同视角**：地缘政治、AI 研究/工程和股票市场三个视角共享事件档案，分别给出因果链、反证、情景、行动与失效信号，再合成共识、分歧和传导链。
- **HTML 先交付**：JSON、Markdown、HTML 完成后立即返回；PDF、可选 Notion 和独立评估作为可重试的后台尾部任务继续执行。
- **每版自动送到桌面**：生成或刷新 HTML 后，额外写入 `daily-intelligence-YYYY-MM-DD-EDITION-rN.html`。桌面副本使用绝对本地图片、日报中心和 PDF 链接，移动到桌面后仍可完整阅读。
- **失败不会伪装成“没有新闻”**：登录、验证码、限流、访问失败和待人工验证都有独立状态；单一来源失败不阻断其余日报。
- **本地事实源优先**：JSON/Markdown 是不可变事实源；HTML/PDF 是可重建阅读投影；Notion 是可选、可续传的远程发布目标。

日报固定包含：

```text
资讯：国际、国内新闻、军事、市场
技术：技术新闻、论文、开源项目
研判：地缘政治、AI 研究与工程、股票市场、跨视角综合
```

## 真实早报示例

以下示例来自连续两天的实际运行，示例文件移除了本机绝对路径，图片仅引用公开来源。GitHub 不直接执行 HTML 时，请下载后用浏览器打开。

| 日期 | HTML 示例 | 覆盖 | 精选研判 | 图片 | 说明 |
| --- | --- | ---: | ---: | ---: | --- |
| 2026-07-24 | [晨报 r3](examples/reports/2026-07-24-morning-r3.html) | 197 条 brief、24 个来源 | 10 个事件、3 个视角 | 57 | 当日补充修订版，展示能源、关税和 AI 资本效率的联动分析 |
| 2026-07-25 | [晨报 r1](examples/reports/2026-07-25-morning-r1.html) | 235 条 brief、29 个来源 | 10 个事件、3 个视角 | 87 | 06:11 生成，展示多通道能源风险、科技监管和智能体工程进展 |

两份示例都保留原题、中文标题、TL;DR、发布时间或明确标注的采集时间、来源排名、公开配图、精选事件引用、三视角研判、本地筛选和反馈下载。

它们是历史实报，不是为文档重写的“完美样稿”。7 月 25 日文件还保留了独立评估对 schema 1.5 和缺少跨视角综合的明确扣分；当前 schema 2.0 流程会在发布前拒绝这类草稿。这也展示了项目的质量边界：评估不会替生成结果掩饰问题，修复由后续代码、契约和下一版报告完成。

## 工作方式

```text
83 个来源
   │  RSS/Atom → 条件缓存 → 静态 HTML → 显式 Edge 验证
   ▼
零 Token 监控快照
   │  时间、图片、去重、来源健康、同事件聚类
   ▼
版本采集与正文富化
   │  HTTP 优先；仅为登录、挑战或 JS 空壳按需启动 Edge
   ▼
并行 brief 写作 + 图片预取
   │  Python 验证并原子合并批次
   ▼
三视角研判 + 跨视角综合
   ▼
JSON / Markdown / HTML → 桌面 HTML
   └──────────────────→ 后台 PDF / Notion / 独立评估
```

关键的状态转换、覆盖目标、校验、持久化和发布都在 Python 中确定性执行；模型只负责选择、中文写作和研判。外部网页始终作为不可信数据解析，不执行其中的指令。

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

安装后直接向 Hermes 发出任务：

```text
使用 daily-intelligence 生成今天的晨报，保存为本地 HTML 和 PDF。
```

```text
使用 daily-intelligence 生成今天的晚报，补充日间变化和次日观察。
```

只有明确要求发布到 Notion 时才会访问 Notion。本地生成、桌面 HTML、PDF 和独立评估都不需要 Notion 凭据。

## 日常使用

刷新零 Token 监控并打开本地情报台：

```powershell
daily-intel refresh-monitor
daily-intel monitor-status
daily-intel serve --open --refresh-minutes 30
```

情报台默认只监听本机，提供新闻流、事件聚类、来源健康和人工验证四个视图。每条新闻按“图片、来源、原题、发布时间（缺失则采集时间）、公开摘要”竖排显示。

手动运行一个版本：

```powershell
daily-intel run-edition --edition morning --profile-dir PROFILE_DIR
daily-intel run-edition --edition evening --profile-dir PROFILE_DIR
```

`run-edition` 优先复用 90 分钟内的新鲜监控快照。需要登录或人工确认时再显式运行：

```powershell
daily-intel verify-pending --index INDEX.json --profile-dir PROFILE_DIR --browser-channel msedge
```

完成模型草稿后，先验证，再让 HTML 前台交付：

```powershell
daily-intel validate-report DRAFT.json --run RUN.json
daily-intel finalize-edition --run RUN.json --report DRAFT.json --defer-tail
daily-intel complete-edition-tail --run RUN.json
```

添加 `--publish` 才会同步 Notion。尾部任务幂等，可用同一命令续跑 PDF、Notion 或评估失败，不撤回已完成的本地日报。

## 输出与桌面交付

| 格式或路径 | 用途 |
| --- | --- |
| `reports/YYYY-MM-DD/EDITION-rN.json` | 不可变结构化事实源 |
| `reports/YYYY-MM-DD/EDITION-rN.md` | 审阅与归档 |
| `reports/YYYY-MM-DD/EDITION-rN.html` | 完整本地阅读投影 |
| `reports/YYYY-MM-DD/EDITION-rN.pdf` | A4 打印与分享 |
| `reports/index.html` | 按日期和修订浏览全部本地日报 |
| `Desktop/daily-intelligence-…html` | 每次生成或评估刷新后的桌面副本 |
| Notion | 显式启用的远程同步 |

默认配置：

```yaml
output:
  formats: [html, pdf]
  pdf_engine: edge
  open_after_finalize: false
  copy_html_to_desktop: true
  desktop_dir:
```

`desktop_dir` 留空时使用当前用户的 `Desktop`；也可设置为任意可写的绝对目录。桌面写入失败会在结果中保留 `desktop_html_error` 和明确 warning，但不会破坏已经落盘的 JSON、Markdown 或本地 HTML。

## 可靠性与边界

- 新报告使用 schema 2.0；读取仍兼容 schema 1.1—1.5 和旧 source-index 的根级/嵌套条目形状。
- 每次任务绑定唯一 `DATA_DIR`，所有 run、索引、正文、报告、评估和连续性状态都留在同一数据根。
- 全文提取、图片下载和来源采集都有全局/同域并发、大小、超时和重试边界。
- 图片按内容哈希缓存；公开 URL、格式、像素、大小和 DNS 安全检查不通过时不会进入报告。
- `NEW` 必须有可解析的来源发布时间；只有采集时间的条目不会伪装成当天新闻。
- 报告发布前校验标题、URL、来源、时间、访问状态、引用、覆盖计数、情景依据和跨视角结构。
- 独立评估绑定 `report_id` 与内容哈希，只给出质量和连续性建议，不修改不可变报告。
- 项目不会绕过登录、验证码、限流、付费墙或站点访问控制，也不会上传浏览器配置、Cookie、登录后 HTML 或账户截图。
- 研判仅用于研究辅助，不构成投资、法律或其他专业建议。

## 配置与文档

- 核心来源和输出配置：[configs/sources.yaml](configs/sources.yaml)
- 零 Token 发现来源：[configs/discovery-sources.yaml](configs/discovery-sources.yaml)
- 日常运行与故障恢复：[references/runbook.md](references/runbook.md)
- Windows 安装与桌面交付：[references/windows-setup.md](references/windows-setup.md)
- Notion 配置：[references/notion-setup.md](references/notion-setup.md)
- 编辑与证据规则：[references/editorial-policy.md](references/editorial-policy.md)
- 架构与状态设计：[references/system-design.md](references/system-design.md)
- 开发者 Wiki：[wiki/Home.md](wiki/Home.md)
- 版本记录：[CHANGELOG.md](CHANGELOG.md)

## 开发与验证

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest
python -m compileall -q src tests
```

修改来源过滤、状态模型、校验、发布或旧格式兼容逻辑时必须同步更新测试。运行时 `data/`、浏览器 profile、Cookie、账号截图和认证 HTML 不得进入仓库。

## License

[MIT](LICENSE) © Wang Mingfeng
