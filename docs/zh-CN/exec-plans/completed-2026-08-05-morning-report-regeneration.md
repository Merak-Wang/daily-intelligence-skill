# 2026-08-05 今日日报重生成与数据流实录

**目的：** 记录 2026-08-05 早报重生成时实际观察到的完整数据流、工件血缘、排序核验、
代码审阅发现、错误版本清理与验证证据。

**状态：** 已验证
**负责人：** 仓库维护者
**最后验证：** 2026-08-05

英文权威版本：
[2026-08-05 Morning Report Regeneration and Observed Data Flow](../../exec-plans/completed-2026-08-05-morning-report-regeneration.md)。

## 最终结果

唯一权威成品是 <code>daily-2026-08-05-morning-r3</code>：403 条普通简报、29 个有内容
来源、7 个非空栏目、8 个精选事件。最终来源/证据索引包含 32 个配置来源和 709 条候选。

用户要求的排序语义已经满足：

- 全局默认仍为 <code>collection.item_order: source</code>；
- 可选的第二种排序仍为 <code>published_at</code>；
- 普通简报严格保持当前 index 与 <code>brief_plan.default_item_ids</code> 的顺序；
- TWZ 显示 <code>来源Top1</code> 至 <code>来源Top15</code>；
- 微博显示 <code>热搜Top1</code> 至 <code>热搜Top15</code>；
- InfoQ 显示 <code>来源Top1</code> 至 <code>来源Top15</code>；
- 普通来源组不再按内部 importance 二次重排。

运行状态为 <code>completed_partial</code>，原因不是简报缺失，而是 4 个来源状态被如实保留：
SEC 与 Reuters 需要验证；Defence Blog 已取得 10 条真实候选后遇到 HTTP 403，因此为
partial；Yahoo 遇到 HTTP 429 限流。403 条计划简报全部存在。

## 证据口径

- **实测：** 直接读取不可变 index、context、写作回执、报告、run manifest、评估和投影文件。
- **复算：** 从工件重新计算总数、顺序、ID 相等性、栏目合计与 Hash。
- **代码审阅：** 沿根级规范实现和聚焦回归测试追踪行为。

规范实现只认 [src/daily_intelligence](../../../src/daily_intelligence/)。<code>build/</code>、
<code>dist/</code>、<code>skills/signaltrail/</code> 和 Hermes 安装目录中的复制品均不作为
实现事实源。

## 端到端数据流

~~~mermaid
flowchart TD
    A["Attempt 4 manifest<br/>10:58:16"] --> B["复用 Monitor 快照<br/>仅当前条目"]
    B --> C["32 个来源采集器<br/>实时 + 合格缓存兜底"]
    C --> D["规范化 / 去重<br/>写入原始 source_rank"]
    D --> E["执行 item_order<br/>默认 source"]
    E --> F["不可变 index r6<br/>709 条"]
    F --> G["紧凑 context r6<br/>565 候选 / 29 计划"]
    G --> H["选择 12 条正文富化"]
    H --> I["HTTP 富化<br/>12 尝试 / 6 正文"]
    I --> J["不可变 index r7<br/>derived_from r6"]
    J --> K["context r7<br/>403 条计划"]
    K --> L["语义缓存边界<br/>386 复用 / 17 新写"]
    L --> M["1 个写作批次<br/>17 条被接受"]
    M --> N["确定性合并<br/>403 条简报"]
    N --> O["研判包<br/>18 个候选"]
    O --> P["研判草稿<br/>8 个精选事件"]
    P --> Q["编译 + 校验<br/>0 错误 / 0 警告"]
    Q --> R["不可变报告 r3<br/>JSON + Markdown"]
    R --> S["HTML + 桌面副本"]
    S --> T["Edge 延迟生成 PDF"]
    T --> U["按规范契约独立评估"]
    U --> V["评估后 HTML/PDF<br/>再次核对 Top 顺序"]
~~~

## 最终工件血缘

| 角色 | 绝对路径 | 含义 |
| --- | --- | --- |
| Run manifest | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/runs/2026-08-05/morning.json</code> | attempt、状态、预算、时间戳、pending 来源与工件指针 |
| 采集 index r6 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/indexes/2026-08-05/morning-r6.json</code> | attempt 4 正文富化前采集结果 |
| 最终 index r7 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/indexes/2026-08-05/morning-r7.json</code> | 709 条最终证据；派生自 r6 |
| 最终 context r7 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/context/2026-08-05/morning-r7.json</code> | 候选、计划、复用和写作边界 |
| 写作 session | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/context/2026-08-05/morning-r7-authoring/session.json</code> | attempt、context Hash、截止时间与接受回执 |
| 研判包 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/context/2026-08-05/morning-r7-authoring/analysis-packet.json</code> | 有界 18 候选输入 |
| 研判草稿 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/context/2026-08-05/morning-r7-authoring/analysis-draft.json</code> | 8 个精选事件与三视角研判 |
| 报告 JSON r3 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/reports/2026-08-05/morning-r3.json</code> | 权威报告 |
| 报告 Markdown r3 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/reports/2026-08-05/morning-r3.md</code> | 文本投影 |
| 报告 HTML r3 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/reports/2026-08-05/morning-r3.html</code> | 本地交互投影 |
| 桌面 HTML r3 | <code>C:/Users/wmf/Desktop/daily-intelligence-2026-08-05-morning-r3.html</code> | 便捷副本 |
| 报告 PDF r3 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/reports/2026-08-05/morning-r3.pdf</code> | 便携投影 |
| 独立评估 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/evaluations/2026-08-05/morning-r3.json</code> | 与报告 ID、内容 Hash 绑定的纠正后评估 |

Index 根级 <code>items[]</code> 是规范事实源，<code>sources[].items[]</code> 是兼容镜像。
r7 两个视图均为 709 条，ID 可完全对账。

## 逐阶段实录

### 1. 运行创建与预算

[prepare_edition](../../../src/daily_intelligence/workflow.py) 在 10:58:16 创建 attempt 4。
采集窗口为 2026-08-04 18:00 至 2026-08-05 06:00，时区 Asia/Shanghai。运行开始前即持久化：

- 最大运行 3,600 秒；
- 最多 12 条正文富化；
- 每个正式来源最多/目标 15 条；
- 截止时间 11:58:16；
- 输出语言 <code>zh-CN</code>；
- 数据根 <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence</code>。

主运行耗时 2,226 秒，<code>deadline_exceeded=false</code>。

### 2. Monitor 快照进入正式采集

本轮复用零模型 Token Monitor 快照。历史保留项只服务连续性，不能占正式 Top 配额。加载器和
采集器会剔除 <code>retained_from_previous_snapshot</code>，最终 r7 根级历史保留项为 0。

当合格缓存中的当前条目不足目标时会触发实时采集。审阅发现旧混合逻辑把缓存置于实时结果之前，
会把刚升到 Top1 的新闻压到后面。修复后，只要实时采集成功返回候选，实时页面即为排序权威，
去重后的 Monitor 条目只能补尾；实时无结果或失败时仍保留缓存兜底。

### 3. 采集、规范化与两种排序

[collect_source](../../../src/daily_intelligence/collector.py) 与专用 adapter 把外部内容按不可信
输入处理，依次执行：

1. URL/标题规范化；
2. canonical URL 去重；
3. 按原始输入位置写入 <code>metadata.source_rank</code>；
4. 执行来源选择的 <code>item_order</code>；
5. 构建逐来源目标/上限；
6. 同步根级事实源与兼容镜像；
7. 不可变落盘。

<code>source</code> 按原始 <code>source_rank</code> 保持当前 Top，历史保留项置后。
<code>published_at</code> 按可解析发布时间倒序，缺失时间或并列时保持稳定输入顺序。两种模式都
不改写 <code>source_rank</code>。

### 4. 32 个来源的完整传播结果

“采集”来自最终 r7 来源组；“入报”来自 r3 普通 briefs。真实候选少于 15 时使用实际数量，
不伪造补位。

| 来源 | 最终状态 | 采集 | 入报 | 保留的采集细节 |
| --- | --- | ---: | ---: | --- |
| <code>weibo_hot</code> | success | 50 | 15 | |
| <code>yicai_economy</code> | success | 40 | 15 | |
| <code>nbs_china_releases</code> | success | 15 | 15 | 有可用条目后发生 HTTP 断连，诊断仍保留 |
| <code>pboc_monetary_reports</code> | success | 1 | 1 | |
| <code>sec_edgar_latest</code> | verification_required | 0 | 0 | HTTP 403 |
| <code>federal_reserve_releases</code> | success | 20 | 15 | |
| <code>hacker_news</code> | success | 20 | 15 | |
| <code>lobsters</code> | success | 25 | 15 | |
| <code>infoq_ai</code> | success | 22 | 15 | 实时结果为权威，缓存仅补尾 |
| <code>anthropic_research</code> | success | 11 | 11 | Feed 发现失败后由公开页面采集 |
| <code>bytedance_seed_papers</code> | success | 20 | 15 | |
| <code>openai_publications</code> | success | 40 | 15 | |
| <code>deepmind_publications</code> | success | 30 | 15 | Feed 发现失败后由公开页面采集 |
| <code>huggingface_papers</code> | success | 13 | 13 | Trending Top 为权威，允许较早论文 |
| <code>papers_with_code</code> | success | 13 | 13 | |
| <code>google_research_publications</code> | success | 16 | 15 | Feed 发现失败后由公开页面采集 |
| <code>microsoft_research_publications</code> | success | 10 | 10 | |
| <code>nvidia_research_publications</code> | success | 32 | 15 | Feed 发现失败后由公开页面采集 |
| <code>arxiv_ai</code> | success | 40 | 15 | |
| <code>github_trending</code> | success | 18 | 15 | |
| <code>cnbc_world</code> | success | 30 | 15 | |
| <code>reuters</code> | verification_required | 0 | 0 | HTTP 401 |
| <code>abc_news</code> | success | 15 | 15 | Feed 发现失败后由公开页面采集 |
| <code>guardian_uk</code> | success | 40 | 15 | |
| <code>bbc_world</code> | success | 29 | 15 | |
| <code>forbes</code> | success | 50 | 15 | Feed 发现失败后由公开页面采集 |
| <code>twz</code> | success | 33 | 15 | |
| <code>defence_blog_aviation</code> | partial | 10 | 10 | 10 条真实候选后 HTTP 403 |
| <code>the_aviationist</code> | success | 15 | 15 | |
| <code>usni_news</code> | success | 30 | 15 | |
| <code>rusi_publications</code> | success | 21 | 15 | |
| <code>yahoo_news</code> | rate_limited | 0 | 0 | HTTP 429 |

合计：32 个配置来源，28 success、2 verification_required、1 partial、1 rate_limited，
709 条候选，29 个来源进入报告。

### 5. Index r6 与 context r6

10:59:18 完成采集并写入不可变 r6。[build_context](../../../src/daily_intelligence/context.py)
没有把所有原始载荷交给模型，而是保持 index 顺序并生成有界下游契约：

- 565 个紧凑候选；
- 29 个来源计划；
- 403 个有序 <code>default_item_ids</code>；
- 每个计划条目的语义缓存决定；
- 缓存复用后只剩一个写作批次。

### 6. 12 条正文富化与 r7

11:00:20 进入正文富化的 ID：

<code>twz-4582ce2507ce</code>、<code>cnbc_world-75fa3f096460</code>、
<code>bbc_world-7bef5564d15e</code>、<code>guardian_uk-9f92cc1a9884</code>、
<code>cnbc_world-53f2c23e264d</code>、<code>cnbc_world-aafa912ecfbf</code>、
<code>yicai_economy-acf17f094d66</code>、<code>nbs_china_releases-f62797899b9f</code>、
<code>openai_publications-c7160f6694ef</code>、<code>twz-b113f21e9de1</code>、
<code>arxiv_ai-60605cbf67b7</code>、<code>arxiv_ai-75507c9762ca</code>。

12 条均走 HTTP，总并发 3、逐域并发 1。成功正文 6 条：TWZ 2、BBC 1、NBS 1、arXiv 2；
CNBC 3、Guardian 1、第一财经 1、OpenAI 1 如实保留验证/无正文结果。未触发浏览器兜底，
耗时 2.642 秒。

富化创建不可变 r7（<code>derived_from</code> r6）并重建 context r7，没有覆盖前版。

### 7. Top 计划与最终报告逐项对账

29 个入报来源的最终 item ID 序列均与 context r7
<code>brief_plan.default_item_ids</code> 完全相等。

TWZ 最终 15 条依次为：

1. <code>twz-4f525db715ab</code>
2. <code>twz-a0624db0745c</code>
3. <code>twz-4582ce2507ce</code>
4. <code>twz-b113f21e9de1</code>
5. <code>twz-1fbe7ee5e5d9</code>
6. <code>twz-4b72c130d70a</code>
7. <code>twz-a01c681a0cde</code>
8. <code>twz-d24e96f556b0</code>
9. <code>twz-abe598680c0f</code>
10. <code>twz-d95ab220cd13</code>
11. <code>twz-83ab8b2e7db3</code>
12. <code>twz-fe0e2c590059</code>
13. <code>twz-0f073a839e39</code>
14. <code>twz-8f5a82ddbe55</code>
15. <code>twz-a40fdfb0fa89</code>

评估刷新后的 HTML 再次解析：共 29 个来源组；TWZ、InfoQ 均为连续
<code>来源Top1</code>…<code>来源Top15</code>，微博为连续
<code>热搜Top1</code>…<code>热搜Top15</code>。

### 8. 语义缓存、写作与确定性合并

[begin_authoring_session](../../../src/daily_intelligence/authoring.py) 把 session 绑定到 attempt 4、
context r7 及其 Hash、403 条计划和运行截止时间。只有 item ID、语义指纹、输出语言、既往评估和
本轮计划成员资格同时满足才允许缓存复用。

- 386 条复用；
- 17 条新写；
- 唯一 packet 恰好包含这 17 个 <code>author_item_ids</code>；
- 11:03:44 接受，回执墙钟耗时 189 秒；
- 合并后 403 条，无 missing/recovered batch；
- 合并耗时 0.025 秒。

批次完成顺序不会决定入报顺序；组装器会重新读取各来源
<code>default_item_ids</code> 构造报告。

### 9. 图片流

异步预取检查 403 个计划条目：146 个候选、121 个预取附着、105 个唯一文件、104 个缓存复用、
25 个失败、0 个预算跳过、23,638,452 bytes、1.694 秒。

最终保存边界再次物化并记录：139 张附着图、123 个唯一文件、104 个复用文件、7 个安全失败、
26,000,222 bytes。7 条最终警告都是非公网地址图片省略，没有新闻被删。

### 10. 研判、修复与校验

研判包含 18 个候选，最终选择 8 个事件，覆盖地缘、AI/技术和市场。结构化证据 ID 均检查为候选集
成员。接受前经历两轮修复：

1. 一个精选事件错误绑定两篇来源，且 stakeholder 缺中文；保留 TWZ 证据并改为
   <code>Anthropic公司</code>；
2. 研判/合成仍引用已移除的 Aviationist 条目；删除两处结构化 evidence ID。

最终预保存校验为 0 错误、0 警告。研判遥测 1,760 秒，总写作遥测 1,990 秒；包含 Agent 等待，
不是纯 CPU 时间。

### 11. 编译、不可变保存与栏目

[compile_report_data](../../../src/daily_intelligence/reporting.py) 恢复确定性字段，把普通 brief
绑定到权威 index/plan，生成 Top 标签，规范化七栏目并执行 Schema、证据和覆盖校验。草稿校验现在
在内存副本注入仅用于校验的 report ID/revision，不会因尚未落盘而误报。

11:35:20 保存：

- report ID <code>daily-2026-08-05-morning-r3</code>；
- 内容 Hash
  <code>e5277bfad7c9af2202c0c507d14e4354f5f56781a14683e044c703371e77f119</code>；
- 403 briefs、29 来源、8 精选事件；
- 119 NEW、16 UPD、268 WATCH；
- 391 metadata_only、6 full_text、6 verification_required。

| 栏目 | 条数 |
| --- | ---: |
| 国际 | 45 |
| 国内新闻 | 15 |
| 军事 | 70 |
| 市场 | 76 |
| 技术新闻 | 45 |
| 值得阅读的论文 | 137 |
| 今日值得关注的开源项目 | 15 |
| **合计** | **403** |

保存耗时：编译/校验 0.153 秒、图片 65.566 秒、不可变持久化 0.076 秒、初始本地投影
1.683 秒、状态 0.178 秒、总计 67.942 秒。

### 12. HTML、PDF 与尾任务

主路径先写 JSON、Markdown、HTML、桌面 HTML 和归档索引。尾任务 11:35:39 开始，
11:38:46 完成；Edge PDF 181.606 秒，11:38:41 ready，无 warning/error。

独立评估随后刷新 HTML/PDF。刷新后再次检查 Top1–15，避免旧渲染器静默恢复 importance 顺序。

### 13. 独立评估与版本边界缺陷

纠正后的独立评估于 14:25:59 不可变落盘，ID 为
<code>evaluation-daily-2026-08-05-morning-r3-r3</code>，总分 36/45。它在确认所有普通来源组
保持规范 Top 顺序后，给 <code>importance_ordering</code> 5/5。连续性决定为
<code>selective</code>，在证据和 TL;DR 缺陷修复前排除 <code>analyses</code> 与
<code>event_summaries</code> 的后续复用；日报本身仍被接受，这两个排除类别用于防止已知缺陷继续
进入未来语义连续性。

第一次调度没有发现报告排序问题，而是暴露了两个版本边界问题：

1. Windows 主机不代表执行 shell 是 PowerShell；Hermes 经 Git Bash 执行，原
   <code>$env:PYTHONPATH</code> 没有把 CLI 绑定到规范源码；
2. 评估器读取旧安装技能契约，错误要求普通 brief 按 importance 重排，与用户要求和当前契约相反。

现已改为 shell 无关的 <code>python -c</code> 启动，在 Python 内注入规范 <code>src/</code>；
提示中使用仓库权威契约绝对路径，并明确普通简报只认 index/brief plan 顺序。聚焦测试同时断言
调度命令不再包含 PowerShell 或 POSIX 的 <code>PYTHONPATH</code> 语法。

## 时间线

| 阶段 | 时间 | 实测变化 |
| --- | --- | --- |
| created | 10:58:16 | attempt 4 manifest 与截止时间落盘 |
| collecting | 10:58:18 | 来源采集开始 |
| building_context | 10:59:18 | 不可变采集 index 已存在 |
| awaiting_selection | 10:59:18 | context r6 暴露选择候选 |
| extracting_content | 11:00:20 | 12 个 ID 进入富化 |
| awaiting_authoring | 11:00:23 | 不可变 r7 index/context 已存在 |
| brief receipt | 11:03:44 | 17 条单批次接受 |
| finalizing | 11:34:14 | 最终报告组装开始 |
| report persisted | 11:35:20 | JSON/Markdown r3 成为权威 |
| completed_partial | 11:35:22 | 本地报告完整，4 来源状态 pending |
| tail started | 11:35:39 | 延迟 PDF 开始 |
| PDF ready | 11:38:41 | 初始 Edge PDF 存在 |
| tail completed | 11:38:46 | 尾任务无错误完成 |
| evaluation refresh | 14:28:47 | 纠正后独立评估与最终 Edge PDF 完成 |

## 本轮代码审阅已修复

| 严重度 | 缺陷 | 影响 | 修复与证据 |
| --- | --- | --- | --- |
| 高 | Monitor 历史保留项可满足正式目标。 | 旧条目占 Top 位且阻止实时补足。 | 加载和采集双重过滤；新增 10 当前 + 10 历史 + target15 回归。 |
| 高 | 实时+缓存混合成功时缓存排在实时前。 | 新晋 Top1 被旧缓存压后；中间版 InfoQ 受影响。 | 实时结果拥有前缀，去重缓存仅补尾；实时失败仍保留缓存兜底。 |
| 高 | 评估器使用旧安装 CLI 与旧契约。 | 正确 HTML/PDF 可被旧 importance 顺序重投影，还会产生错误评分。 | shell 无关源码启动、权威契约绝对路径、明确 plan 顺序、聚焦测试。 |
| 中 | resume merge 把重试来源追加到末尾。 | 重试后来源组顺序变化。 | 在原位置替换；A/B/C 重试 B 后仍为 A/B/C。 |
| 中 | 草稿校验要求已持久化 report ID/revision。 | 合法未保存草稿被误拒。 | 深拷贝并注入仅校验身份，不修改输入。 |
| 低 | context docstring 暗示富化会提升排序。 | 文档与规范顺序不一致。 | 修正文档契约。 |

## 已记录但未扩张处理的缺口/优化

- 富化阶段进程级失败后尚不能检查点恢复；
- 写作 packet 可覆盖，未逐包 Hash 绑定 context；
- 采集健康度与缓存可用性尚未完全分离；
- context compact 可能漏掉低于前缀上限的显式富化项；
- JSON/Markdown 尚非同一修订事务；
- 评估派生刷新重试尚未完全幂等；
- 批次耗时起点偏早，失败修复尝试未持久保留；
- 实时 transport 指标仍合并 browser/HTTP；
- Monitor 资格与投影 ready 不是同一事实检查；
- <code>published_at</code> 只能排序 adapter 已返回的有界候选集；
- 独立 <code>save-report</code> 可绕开 run 持有的 brief-plan 边界；
- 多页验证合并语义需要更强刻画测试；
- 每包 45 条只是软目标，不是硬上限；
- 最终 PDF 约 99 MB，需要图片降采样/嵌入优化；
- 语义缓存复用了 9 条带 packet/数据包内部措辞的 TL;DR；
- 连续性研判应比较紧邻上一修订，且所有正文内 evidence ID 都应机器校验。

持久化条目同步写入[技术债追踪器](tech-debt-tracker.md)。

## 错误代码/报告版本清理

仓库只有一个本地 <code>main</code> 分支、一个 worktree，无 stash 或游离工作副本。看起来的“很多
代码版本”实际是生成/安装复制品，不是 Git 分支。

收尾时下列 ignored 目标已送入 Windows 回收站，可恢复：

- <code>E:/ai_project/daily-intelligence-skill/build</code>；
- <code>E:/ai_project/daily-intelligence-skill/dist</code>；
- <code>E:/ai_project/daily-intelligence-skill/output/hermes-tap-smoke</code>；
- <code>E:/ai_project/daily-intelligence-skill/output/signaltrail-tap-premerge-backup</code>。

规范 <code>src/</code>、测试、文档、用户原有脏改动、受 Git 跟踪的
<code>skills/signaltrail/</code> 发布快照以及最终 r3 血缘均保留；发布快照不会手工修改。

错误的用户可见 r1 和中间 r2 文件没有永久删除，而是移到
<code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/retrospectives/2026-08-05/invalid-revisions/reports</code>。
两份旧契约评估和共享草稿移到同级 <code>evaluations</code> 审计目录。重建后的本地归档在
2026-08-05 只剩一张卡片：morning r3。

正式安装器随后同步
<code>C:/Users/wmf/AppData/Local/hermes/skills/research/signaltrail</code>；31 个已安装 Python
模块的 Hash 均与规范源码一致。三处旧 <code>daily-intelligence</code> 技能目录已送入 Windows
回收站；<code>hermes skills list</code> 对本工作流只显示已启用的 <code>signaltrail</code>。

## 最终验证

- r7 根级 709 条，兼容镜像 709 条；
- 根级历史保留项 0；
- context 候选/计划/计划条目：565 / 29 / 403；
- 语义复用/新写/批次：386 / 17 / 1；
- 报告 briefs/来源/精选事件：403 / 29 / 8；
- 29 个来源的报告 ID 顺序全部等于 plan 顺序；
- 评估后 HTML 的 TWZ、微博、InfoQ 均连续 Top1–15；
- Schema/报告校验 0 错误、0 警告；
- 未超过运行截止时间。

最终文件 Hash：

| 工件 | Bytes | SHA-256 |
| --- | ---: | --- |
| 报告 JSON r3 | 735,242 | <code>f9ce3a794fbaf8012ea09774264aff062184ddcbf7b598265d12408da3ed5e87</code> |
| 报告 Markdown r3 | 252,164 | <code>5ee7f53506141e3b3a47d01eb7f7bc809585174d6f592d0237c3e9bea6770210</code> |
| 报告 HTML r3 | 542,936 | <code>cd7c658730562454a0172d99eb86acec53fff1cf4e2312bc7eee971936258f9b</code> |
| 报告 PDF r3（155 页） | 98,974,490 | <code>8e6e10d286d241d59338d5bbda902e176d0c00eefffd7d6fe99a9e19710a637a</code> |
| 独立评估 r3 | 5,500 | <code>43ce71a98b333d5d0ac1515ad847c812681790bfa6d000df7a828add97e376b6</code> |
| 最终 index r7 | 2,547,312 | <code>846f5b764ea859de1e22a5ee29a7ba86e1ab9180ee3244a5b0ff4b91e42a7bd9</code> |
| 最终 context r7 | 1,215,194 | <code>00273c941f49c7260da29584a14b946050d2e54ec1eec4db4c92258542a0980e</code> |

仓库门禁：

~~~powershell
python -m pytest
python -m ruff check .
python -m compileall -q src tests scripts
python scripts/check_code_comments.py
python scripts/check_docs.py
git diff --check
~~~

最终结果：236 项测试在 24.37 秒内通过；Ruff、compileall 通过；34 个维护中 Python 文件通过
中文语义注释门禁；文档检查通过 10 个规范记录和 49 个 Markdown；
<code>git diff --check</code> 通过。安装后的 CLI 另行返回报告校验
<code>{"errors": 0, "warnings": 0}</code>。

## 恢复策略

- 清理的运行/代码复制品可从 Windows 回收站恢复；
- 最终保留链为 report r3、index r6/r7、context r7、写作回执、run manifest 和纠正后评估；
- 为解释语义复用而必须保留的中间事实继续存在，但错误的用户可见投影不留在归档入口；
- 若只需重建投影，以 r3 JSON 为事实源并使用根级规范源码，不要为刷新 HTML/PDF 重写语义内容。
