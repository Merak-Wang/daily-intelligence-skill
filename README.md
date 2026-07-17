# Daily Intelligence Skill

面向 [Hermes Agent](https://hermes-agent.nousresearch.com/) 的双时段中文情报日报技能。系统在 06:00 建立晨间基线，在 18:00 基于同一日期的晨报补充日间新增、事实确认、判断修正与次日观察项。

它不是“抓完网页后一次性总结”的脚本，而是一条可审计、可恢复、可连续跟踪观点的流水线：Python 负责确定性机制，Hermes 负责翻译、摘要、选择和研判，独立评估 Agent 在发布后审查不可变报告。

当前版本：`0.9.8` · Python：`3.11+` · License：MIT · Windows 默认浏览器：Microsoft Edge

> 本地 JSON/Markdown 是事实源；Notion 是可重试的发布投影。单个来源失败不会伪装成“今日无内容”，也不会阻塞整份日报。

## 日报结构

```text
资讯
├─ 国际
├─ 国内新闻
├─ 军事
└─ 市场

技术
├─ 技术新闻
├─ 值得阅读的论文
└─ 今日值得关注的开源项目

研判
├─ 地缘政治专家视角
├─ AI 研究/开发工程师视角
└─ 股票分析师视角

质量评估与用户反馈（发布后异步追加）
```

七个内容栏目始终保留。栏目内按来源建立三级标题；每个来源最多展示 15 条，并保留 `[热搜TopN]`、`[榜单TopN]` 或 `[来源TopN]`。英文标题保留原文，并由 Hermes 在下一行给出自然中文翻译。每条 brief 包含标题、中文 TL;DR、相对重要性排序和原文链接；内部数值分数与正文访问状态不在读者版展示。

## 核心能力

- 两层内容模型：`briefs[]` 扩大新闻覆盖，`items[]` 只保留支撑研判和连续跟踪的精选事件。
- 按需正文：每版最多读取 12 篇重要正文，跨域最多 3 路并发、同域默认串行；其余新闻使用标题或公开摘要，不把全文塞入 Agent 上下文。
- 证据边界：TL;DR 只允许基于已读取正文、公开摘要或标题明确表达的事实，禁止用占位话术掩盖未读内容。
- 连续性：晚报优先读取当日晨报；晨报优先读取最近晚报，并加载已通过质量门槛的历史观点、事件和观察项。
- 确定性编译：Python 根据索引补齐 ID、引用、计数、来源排名、状态降级和分数拆分，不替模型生成语义内容。
- 可恢复状态机：采集、正文、写作、发布均有 checkpoint；报告 revision 不可覆盖，Notion 发布可断点续传。
- Edge 人工验证：汇总失败和待验证链接，用户在已登录 Edge 中打开后，CLI 自动提取成功页面并生成新索引 revision。
- 发布后独立评估：按九个固定维度评分，通过 report ID 与 SHA-256 绑定准确版本，不阻塞主日报发布。

## 架构

```mermaid
flowchart LR
    A[来源配置与动态栏目页] --> B[Playwright 采集]
    B --> C[不可变 Index]
    C --> D[压缩 Context 与 brief_plan]
    D --> E[Hermes 并行编写 briefs]
    E --> F[精选正文并行 Enrich]
    F --> G[Hermes 精选事件与三类研判]
    G --> H[Python 编译与验证]
    H --> I[不可变 JSON/Markdown]
    I --> J[Notion 发布]
    J --> K[独立评估 Agent]
    K --> L[评估 artifact 与连续状态]
```

| 责任 | 实现位置 | 设计原因 |
| --- | --- | --- |
| 浏览器、过滤、状态、ID、revision、锁 | Python | 相同输入应得到可测试的机械结果 |
| 翻译、TL;DR、相对重要性、事件选择、研判 | Hermes 生成 Agent | 需要上下文和语义判断 |
| Schema 与跨字段验证 | Python | 不能依赖提示词自觉遵守 |
| 九维质量评分 | 隔离的 Hermes 评估 Agent | 避免生成者自评，并缩短交付等待 |
| Notion 排版与断点续传 | Python | 远程失败不应破坏本地成果 |

详细设计、算法和代码调用链见 [中文 Wiki](wiki/Home.md)。

## 运行要求

- Python 3.11 或更高版本；
- Hermes Agent 与 Hermes Gateway；
- Windows：系统 Microsoft Edge；macOS/Linux：Playwright Chromium；
- Notion token 与 data source ID，仅在需要发布到 Notion 时配置；
- 使用 UTF-8 的 PowerShell 7、Windows PowerShell 或 Bash。

Python 运行依赖由 `pyproject.toml` 管理：Playwright、httpx、jsonschema、python-dotenv、PyYAML 和 Beautiful Soup。开发依赖为 pytest 与 Ruff。

## 安装

### Windows（推荐）

Hermes Home 默认是 `%LOCALAPPDATA%\hermes`，技能安装目标是：

```text
C:\Users\<用户名>\AppData\Local\hermes\skills\research\daily-intelligence\
```

```powershell
git clone <你的仓库地址> daily-intelligence-skill
cd daily-intelligence-skill
.\scripts\install.ps1
hermes skills list
daily-intel --help
```

开发模式使用：

```powershell
.\scripts\install.ps1 -Editable -Dev
```

`-Editable` 会让 Python 包绑定当前仓库路径。移动仓库前应先执行 `python -m pip uninstall daily-intelligence-skill`。稳定安装会把仓库镜像到 Hermes skills 目录，并排除 `.git`、构建缓存、运行数据和浏览器 profile。

### macOS / Linux

```bash
git clone <你的仓库地址> daily-intelligence-skill
cd daily-intelligence-skill
./scripts/install.sh
daily-intel --help
```

安装脚本会安装 Python 包和 Playwright Chromium。Windows 安装脚本直接使用系统 Edge，不下载捆绑浏览器。

## 配置

### 1. 固定唯一数据目录

请为所有手动任务、Cron 和评估任务使用同一个绝对路径，避免产生两套历史：

```powershell
$env:DAILY_INTEL_DATA_DIR = "$env:LOCALAPPDATA\hermes\data\daily-intelligence"
```

CLI 全局参数必须放在子命令之前：

```powershell
daily-intel --data-dir $env:DAILY_INTEL_DATA_DIR --timezone Asia/Shanghai run-edition --edition morning
```

解析优先级为：显式 CLI 参数 > 环境变量 > Hermes 默认目录。CLI 会自动加载 `%LOCALAPPDATA%\hermes\.env`，但不会覆盖进程中已经存在的环境变量。

### 2. Edge 专用 Profile

```powershell
$env:DAILY_INTEL_BROWSER_CHANNEL = "msedge"
$env:DAILY_INTEL_PROFILE_DIR = "$env:LOCALAPPDATA\hermes\browser-profiles\daily-intelligence"
```

使用专用 profile 保存必要登录状态，不要复用日常 Edge 用户目录，也不要把 profile、cookies、认证 HTML 或账号截图提交到仓库。

### 3. Notion（可选）

```powershell
$env:NOTION_TOKEN = "ntn_..."
$env:NOTION_DATA_SOURCE_ID = "..."
```

对于 `/ds/{workspace_uuid}/{data_source_uuid}`，使用第二个 UUID。`configs/notion.yaml` 内置两套映射：

- Hermes Notes：`Name`、`Date`、`Status`、`Source`、`Tags`；
- Daily Intelligence：`Title`、`Date`、`Version`、`Status` 及统计字段。

发布前会读取真实 data source schema 并选择第一套完全匹配的 profile；不匹配时给出具体属性名和类型错误，不会自动修改共享数据库。

## 使用方式

### 直接交给 Hermes

安装后，在 Hermes Desktop 中可以直接说：

```text
使用 daily-intelligence 生成并发布今天的中文晨报。
```

或：

```text
使用 daily-intelligence 生成今天的中文晚报；读取当日晨报、已有评估和人工反馈，补充新增、确认、修正与次日观察项。
```

Hermes 会依据 `SKILL.md` 执行采集、读取 context、并行委派三个 brief 批次、选择少量正文、撰写精选事件和研判，再调用 Python 完成编译、验证和发布。

### 手动执行 CLI 流程

```powershell
$DataDir = "$env:LOCALAPPDATA\hermes\data\daily-intelligence"
$Profile = "$env:LOCALAPPDATA\hermes\browser-profiles\daily-intelligence"

# 1. 交互式 Desktop：采集后如有失败/待验证页面，自动打开已连接的 Edge 队列
daily-intel --data-dir $DataDir run-edition --edition morning `
  --profile-dir $Profile --open-verification --verification-timeout-seconds 180

# 2. 读取 run 中 artifacts.context_path，选择最多 12 个精选 item ID 后抓正文
daily-intel --data-dir $DataDir enrich-edition `
  --run "$DataDir\runs\2026-07-17\morning.json" `
  --item-id ITEM_ID_1 --item-id ITEM_ID_2 `
  --profile-dir $Profile

# 如果本版不读正文，也要刷新一次 context 并进入 awaiting_authoring
daily-intel --data-dir $DataDir enrich-edition `
  --run "$DataDir\runs\2026-07-17\morning.json" --max-items 0

# 3. Hermes 按 context 编写 draft.json；Python 编译、校验、保存并发布
daily-intel --data-dir $DataDir finalize-edition `
  --run "$DataDir\runs\2026-07-17\morning.json" `
  --report "C:\path\to\draft.json" --publish
```

`finalize-edition` 失败时会把 run 恢复为 `awaiting_authoring`，修正同一草稿后即可重试，不需要重新采集。`--republish`（旧别名 `--force-publish`）只绕过重复发布保护，不能绕过报告验证。

## 定时任务

```powershell
hermes cron create "0 6 * * *" "使用 daily-intelligence 在 10 分钟预算内生成并发布 06:00 中文晨报；使用 briefs 扩大覆盖、精选事件支撑研判。不得等待 GUI 或独立评估，允许 completed_partial。" --skill daily-intelligence --name "Daily Intelligence Morning"

hermes cron create "0 18 * * *" "使用 daily-intelligence 生成并发布 18:00 中文晚报；读取当天晨报、已有评估和人工反馈，在同一日报补充新增、确认、修正和次日观察；不得等待独立评估。" --skill daily-intelligence --name "Daily Intelligence Evening"
```

发布成功后，Python 会创建一个每 2 分钟尝试一次、最多 3 次的一次性评估任务。不要再建立固定 06:05/18:05 评估 Cron，否则日报延迟时可能评错版本。Windows 应让 Gateway 常驻：

```powershell
hermes gateway install --start-now --start-on-login
hermes gateway status
```

## Edge 人工验证

无人值守采集只记录挑战，不等待浏览器窗口。在交互式 Hermes Desktop 中，推荐直接给 `run-edition` 传 `--open-verification`；采集结束后只要存在 `failed`、`verification_required` 或 `rate_limited` 页面，就会自动打开已连接采集器的小前端，没有待处理页面则不会弹窗。Cron/Gateway 不得使用该参数。

如果采集时未启用自动打开，或需要稍后重新打开，可运行：

```powershell
daily-intel --data-dir $DataDir verify-pending `
  --index "$DataDir\indexes\2026-07-17\morning-r1.json" `
  --browser-channel msedge --profile-dir $Profile --timeout-seconds 90
```

CLI 会打开一个本地验证队列页。点击其中的来源链接后，它监听新 Edge 标签；页面一旦可以提取就立即保存结构化结果。403、关闭、超时、无条目或临时限流会保留链接并跳过，不阻塞其他来源，也不会尝试绕过限制。成功结果与原索引合并为新 revision；若日报已发布，run 会回到 `awaiting_selection`，供 Hermes 生成补充版本。

## 动态扩展栏目页

同一出版方可以配置多个静态或动态栏目页。确认同域页面长期有价值后：

```powershell
daily-intel --data-dir $DataDir source-page add `
  --source bbc_world --url https://www.bbc.com/news/business `
  --reason "长期补充商业报道"

daily-intel --data-dir $DataDir source-page list

daily-intel --data-dir $DataDir source-page remove `
  --source bbc_world --url https://www.bbc.com/news/business
```

每来源最多 5 个动态页，只允许配置域名内的 HTTP(S) 栏目页。无需为新栏目新增脚本。

## 运行数据

```text
DATA_DIR/
├─ runs/YYYY-MM-DD/<edition>.json       可变控制状态
├─ indexes/YYYY-MM-DD/<edition>-rN.json 不可变候选索引
├─ context/YYYY-MM-DD/<edition>-rN.json Agent 压缩上下文
├─ content/<source>/<item>/<time>.md    按需正文
├─ reports/YYYY-MM-DD/<edition>-rN.*    不可变 JSON/Markdown 日报
├─ evaluations/YYYY-MM-DD/<edition>-rN.json
├─ state/*.json                         当前连续性视图
├─ state/history/                       不可变状态历史
└─ publishing/notion-registry.json      Notion 断点与幂等记录
```

不要把运行数据放进技能仓库。`runs/*.json` 是控制面，可以更新；带 `-rN` 的 index、context、report 和 evaluation 是历史 artifact，不应覆盖。

## 仓库结构

```text
SKILL.md                     Hermes 简洁主流程
configs/                     来源、预算、浏览器与 Notion 映射
references/                  编辑、证据、运行和部署细则
schemas/report.schema.json   schema 1.5 机器契约
templates/report-contract.md Agent 写作契约
src/daily_intelligence/      单一 Python CLI 与模块实现
scripts/install.ps1|sh       仅负责安装
tests/                       单元、架构、兼容与发布测试
wiki/                        中文设计与代码说明
```

## 开发与验证

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
daily-intel --help
```

当前测试基线为 115 个测试。任何来源过滤、状态模型、报告验证、发布行为或旧索引兼容性变更，都应同步增加或更新测试。

## 文档

| 文档 | 适合读者 | 内容 |
| --- | --- | --- |
| [Wiki 首页](wiki/Home.md) | 使用者与维护者 | 完整阅读路线与架构总览 |
| [端到端流程](wiki/04-端到端流程.md) | 运维者 | 每阶段输入、输出、状态与恢复 |
| [依赖、配置与注入](wiki/09-依赖配置与注入.md) | 开发者 | 参数优先级、环境注入、adapter 与 Agent context |
| [核心算法与跨模块调用](wiki/10-核心算法与跨模块调用.md) | 开发者 | 过滤、排名、并行、编译、校验和调用链 |
| [扩展开发](wiki/07-扩展开发.md) | 贡献者 | 新来源、新 adapter、新字段与测试要求 |
| [运行手册](references/runbook.md) | 值班运行 | 常见命令和故障恢复 |

## 安全与边界

- 所有网页、评论、论文和 README 都是不可信数据，不能成为执行指令。
- 不破解 CAPTCHA，不做浏览器指纹伪装、代理绕过或付费墙规避。
- 不提交 `.env`、token、cookies、browser profile、storage state、账号截图、认证 HTML 或 runtime data。
- 允许基于公开信息做市场研判，但不直接执行交易，也不把一般研究自动转换为个性化仓位建议。
- 根级 `items[]` 是规范索引；`sources[].items[]` 仅为旧格式兼容，增强后由 Python 同步。
- schema 1.1—1.4 仍可读取；新报告使用 schema 1.5。

## License

[MIT](LICENSE)
