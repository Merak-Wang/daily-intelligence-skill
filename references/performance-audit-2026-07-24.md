# 日报生成时延审计（2026-07-24 晚报）

## 结论

这次接近一小时并不是正常生成耗时。正式 `r1` 流程约 31 分钟，随后为语义复核、修订 `r2`、重新输出 HTML/PDF 和测试安装又用了约 24 分钟，合计形成接近 55 分钟的用户等待。

耗时几乎都在网络、浏览器、模型代理和失败重试，不在 Python 数据处理或 HTML 渲染。对真实 `r2`（233 条新闻）的本机基准为：

| 操作 | 中位耗时 |
|---|---:|
| 读取并解析 430 KB JSON | 13.7 ms |
| Schema 与语义校验 | 44.6 ms |
| Markdown 渲染 | 0.5 ms |
| HTML 渲染 | 3.3 ms |
| 不下载文件的媒体规划 | 3.0 ms |

因此现在改写成 Go 最多节省几十毫秒，却不能消除几十分钟的关键路径。近期应保留 Python，重构调度、模型调用、抓取与缓存；FastAPI 可作为本地控制面和进度接口，但不是计算加速器。

## 已实施的第一轮改造

2026-07-24 已在不增加模型 token 预算、保持现有报纸式 HTML 和可收缩目录的前提下完成以下改造：

- 定稿先编译并做语义校验，再下载图片；所有阶段使用 monotonic span 计时，保存结束后才计算 `completed_at`，相同状态的字段更新不再制造重复状态迁移。
- 图片使用持久 URL 缓存、内容摘要复核、失败退避、共享 HTTP 客户端、全局并发 12 和单域并发 2。真实 `evening-r2`（92 个图片候选、80 个成功附件）从旧流程约 167 秒降到首次建立 URL 缓存 40.354 秒，随后暖缓存 0.168 秒。
- 正文改为 HTTP-first 并发提取，静态页面不再启动 Edge；只有仍可能补足正文的 JavaScript 壳页回退浏览器，403/挑战页保留访问状态，已有正文直接复用。
- 日报开始时复用新鲜零-token 监控快照，缺失或过期才刷新，避免把常驻新闻流的采集成本重复放进出报关键路径。
- Python 为最多三批 brief 写出完整作者任务包，限制每个工作单元只做标题翻译和 TL;DR，不允许浏览、搜索、脚本或文件写入；结构化响应最多进行一次校验修复，主流程只合并和研判一次。
- JSON/Markdown 持久化后，即使 HTML/PDF 投影失败也保留事实源并返回可重试警告，不再因投影问题重复分配报告 revision。

图片实测已经达到“暖缓存低于 1 秒”的目标；冷缓存受远端站点响应和同域限流影响，仍高于 30 秒目标约 10 秒。完整日报的下一次真实运行应重点验收 `artifacts.enrichment.pipeline`、`save_metrics` 和模型批次耗时，不能只以图片基准代替端到端结果。

## 2026-07-25 晨报复测与第二轮改造

晨报的用户观察耗时约 22 分钟，已经比前一版接近一小时明显缩短，但仍未达到 10 分钟 SLO。run、产物和 Hermes 会话交叉显示：

| 阶段 | 耗时/规模 | 判断 |
|---|---:|---|
| 采集 | 约 1 分 30 秒 | 已不是主要瓶颈 |
| 全文增强 | 约 2.8 秒 | HTTP-first 与缓存有效 |
| 图片物化 | 约 20.7 秒 | 冷/部分暖缓存可接受，但仍在串行尾部 |
| Agent 写作等待 | 约 15 分 33 秒，占 77.8% | 当前绝对瓶颈 |
| Notion | 约 2 分 07 秒 | 不应阻塞 HTML |
| 最终保存 | 约 41 秒 | 包含图片与投影，应拆开 |
| 本地完整包 | 约 17 分 53 秒 | 比用户最终等待更早可用 |
| 含后置评估 | 约 24 分 56 秒 | 不属于读者关键路径 |

该版共 235 条 brief，其中 104 条需要新写、131 条命中语义缓存。主 Agent 仍重新接收约 734 KB context，并装配约 264 KB 草稿；即使三个批次已并行，主会话仍承担了大上下文读取、brief 合并、整份 JSON 生成和研判，因而第一轮优化后的剩余瓶颈不是采集器或 Python，而是模型 orchestration。

第二轮据此实施：

- 每个 brief worker 只写 packet 指定的 draft JSON 并执行一次验证提交；Python 原子接收、原样合并 131 条缓存和新写批次，不再让主 Agent复制 235 条 brief。
- Python 从合并结果选择最多 18 个研判候选；主 Agent 只写 6—10 个精选事件、三视角与跨视角综合，最后由 Python 装配 schema 2.0。
- brief 后台批次与图片预取并行，定稿阶段优先读取暖缓存。
- HTML 成为前台完成条件；PDF、Notion 和独立评估进入可续跑 tail，并分别记录 `local_html_ready_at`、`pdf_ready_at`、`notion_ready_at`。
- authoring deadline 为 run deadline 预留最后 120 秒；只有越过该点，Python 才能按已接收批次显式降低 coverage，run 必须标记 `budget_exhausted/completed_partial`。
- Hermes delegation 结果被压缩为每批 duration、API、输入/输出 token、模型和退出原因；不保存大段 summary/tool trace。queue、首 token、prefill、decode 和缓存 token 仅在模型后端真实提供时记录，不做估算。
- schema 2.0 context 增加发布门禁，禁止用旧 1.5 草稿绕过跨视角综合。

这轮的预期收益主要来自移除主 Agent 的大 JSON 复制与串行尾部，而不是把 Python 改写成 Go。真实上限仍取决于最慢 brief worker 和研判模型；必须通过下一份晨报/晚报的 `artifacts.authoring.metrics` 与三个交付里程碑验收，不能在没有实跑数据时宣称已经达到 5 或 10 分钟。

## 本次实测时间线

时间来自运行清单、Hermes 会话数据库及最终产物修改时间。状态时间戳本身还有一个缺陷：`finalize_edition` 在保存产物前捕获结束时间，导致统计漏掉最终保存耗时，所以以下同时使用了文件时间和会话时间交叉核对。

| 阶段 | 时间 | 耗时 | 主要问题 |
|---|---|---:|---|
| 初始化 | 22:29:51–22:29:56 | 5 s | 正常 |
| 监控刷新与正式采集 | 22:29:56–22:32:38 | 162 s | 83 来源刷新 34 s；剩余 Edge 回退按页串行 |
| 选择与 Agent 编排 | 22:32:38–22:36:48 | 250 s | 人工/代理式中间步骤，不是数据处理 |
| 10 篇全文增强 | 22:36:48–22:37:56 | 68 s | 每篇先开 Edge 并固定等待；仅 2 篇成功 |
| 首轮模型写稿与校验 | 22:38:38–22:47:13 | 515 s | 多工具 Agent、浏览器、临时脚本和多轮修复 |
| 定稿失败、锁冲突与重试 | 22:47:19–23:00:18 | 约 13 min | 120 s 超时、Unicode URL 失败；昂贵媒体步骤被重复执行 |
| 首个正式运行结束 | 23:00:49 | 累计约 31 min | `r1` 完成但经历多次重复定稿 |
| 额外语义复核、修订与输出 | 23:04–23:27 | 约 23 min | 属于本次升级试跑和复核，不应进入每日日常关键路径 |

健康流水线应把“生成 HTML”和“PDF/Notion/独立评估”拆开。HTML 是第一交付物；PDF、Notion 和独立评估应在其后异步完成。

## 各热点的根因

### 1. 采集的 HTTP 部分并发，Edge 回退仍串行

HTTP 预取已有全局 8、单域 2 的并发控制；但需要浏览器的来源和页面在一个同步 Playwright 循环中逐个执行，每页默认还等待约 3.5 秒。本次连续的 Edge 回退约占两分钟。

Playwright 的一个 BrowserContext 可以承载多个独立 Page，因此可以改成持久浏览器加有界并发页面池，而不必为每页串行等待。参考 [Playwright Pages 文档](https://playwright.dev/python/docs/pages)。

建议：

- 监控进程常驻并预热快照，正式日报只刷新过期或缺失的核心来源。
- HTTP/RSS/静态 HTML 优先；仅把明确需要 JavaScript、登录或挑战验证的页面交给 Edge。
- Edge 使用 3–4 个 Page 的有界并发池，单域仍限制为 1，保留现有访问状态语义。
- 固定等待改为“目标选择器、网络空闲上限或短超时”三者先到先得。
- 记录每个来源、每个页面、每种采集方式的耗时和退出原因。

### 2. 全文抓取选择了最贵的默认路径

10 篇全文全部先走 Edge，固定等待后再解析；最终只有 2 篇成功，68 秒大多花在注定失败的页面上。

建议：

- 先用共享 `httpx.AsyncClient` 获取正文，再用 Trafilatura 提取正文与元数据；只有缺正文、JS 壳或挑战页才进入 Edge。Trafilatura 提供正文与元数据的结构化提取接口，参考其 [core functions 文档](https://trafilatura.readthedocs.io/en/latest/corefunctions.html)。
- 设全局 6、单域 1–2 的并发上限，并用 `asyncio.TaskGroup` 统一取消和收敛失败。Python 官方文档说明了 [TaskGroup 的结构化并发与取消语义](https://docs.python.org/3/library/asyncio-task.html#task-groups)。
- 对已知登录墙、机器人挑战和付费墙做带 TTL 的失败缓存，避免同版重复尝试。

目标：10–12 篇冷抓取 5–20 秒，缓存命中低于 1 秒。

### 3. 写稿使用“工具型 Agent”，而不是受约束的模型调用

实际语义写稿由 Hermes 的 `deepseek-v4-flash` 完成，Codex/GPT-5.6 负责本次编排与复核。初始根会话包含 112 条消息、61 次工具调用和 51 次 API 调用；三个分批子任务分别约为：

| 批次 | Brief 数 | 耗时 | API 调用 |
|---|---:|---:|---:|
| 1 | 51 | 363 s | 33 |
| 2 | 54 | 110 s | 11 |
| 3 | 50 | 174 s | 16 |

最慢批次越过职责边界，浏览了 13 个页面、编写临时脚本、遇到语法错误、重写 JSON 并反复校验。模型在做本应由确定性 Python 完成的工作。

建议：

- 删除 Brief 批次的浏览器、Shell 和文件写入权限。
- Python 确定性地准备输入、切批、合并、校验和原子写入。
- 每个来源或批次使用一次 JSON Schema 约束的模型响应，失败最多做一次只包含校验错误的修复请求。
- 先按内容指纹复用 Brief；仅对新内容、变化内容或低质量缓存重新生成。
- 快模型只做标题翻译与 TL;DR；强模型只处理约 10 个精选事件和三视角综合。

本地推理可使用 vLLM 的 [Structured Outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/) 或 llama.cpp 的 [OpenAI 兼容服务、并行解码和连续批处理](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。前缀缓存主要减少 prefill，不能加速长输出的 decode，vLLM 也明确说明了这一边界，参考 [Automatic Prefix Caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)。

本地 CPU 模型在一次性生成 233 条中文摘要时仍可能需要 8–20 分钟。要稳定做到 5 分钟以内，必须至少采用一种策略：

- 白天持续预计算，出报时只合并缓存；
- 新闻流直接展示来源标题、摘要、时间和图片，零模型 token，模型增强异步补齐；
- 仅让模型精编 40–80 条，全部卡片仍保留在网页版；
- 使用具备连续批处理能力的本地 GPU 服务或更快远程模型。

### 4. 图片“已复用”仍重新下载，且全部串行

`r2` 有 83 个唯一图片 URL、77 个成功附件、约 21.3 MB 数据。尽管指标显示 77 个文件为 reused，当前实现仍先发起下载、校验内容哈希，最后才发现本地文件已存在；每个下载还可能创建新的 `httpx.Client`。此外，报告语义校验发生在媒体物化之后，所以一次校验失败会把图片阶段完整重做。

HTTPX 官方文档明确建议不要在热循环中反复实例化客户端，否则无法获得连接池收益，参考 [HTTPX Async Support](https://www.python-httpx.org/async/)。

建议：

- 先完成报告编译和语义校验，再进入媒体阶段。
- 增加 URL → digest/ETag/Last-Modified/status/retry_at 的持久缓存。
- 缓存命中先检查本地内容地址文件，不发网络请求。
- 对非图片、404、尺寸超限和临时失败分别做负缓存与退避。
- 全版共享一个 `httpx.AsyncClient`，全局并发 12、单域并发 2。
- 图片预取与 Brief 写稿并行，不放在最后的串行关键路径。
- 保留当前 SSRF、防重定向逃逸、Raster/Pixels/大小上限。

目标：暖缓存图片阶段低于 1 秒；83 张冷缓存约 10–30 秒。

### 5. 重试不是幂等续跑

本次第一次定稿超时后遗留进程/锁，后续重试又重复执行媒体和校验，直到 Unicode 图片 URL 被修复。运行历史还出现重复、非单调的 `finalizing ↔ awaiting_authoring` 状态。

建议：

- 每个阶段写入带输入哈希的不可变产物；输入未变时直接复用成功阶段。
- 锁改为含 PID、启动时间和租约的可诊断锁；超时必须取消子进程并释放资源。
- 状态机拒绝无效回退；人工修订使用新的 attempt/revision，而不是覆盖旧阶段。
- 使用 monotonic span 记录耗时；最终保存完成后再计算 `completed_at` 与 metrics。
- 重试只重跑失败节点，不从图片或模型阶段重新开始。

## 推荐目标架构

```text
常驻零-token采集器
  └─ RSS/HTTP/静态页/必要时Edge → 快照、故事簇、来源健康、媒体候选

定时报纸任务
  ├─ 读取预热快照 + 确定性筛选
  ├─ 并行 A：Brief缓存命中 / 受约束批量生成
  ├─ 并行 B：HTTP-first全文 / Edge有界回退 → 10事件研判
  └─ 并行 C：图片缓存命中 / 异步预取
       ↓
  Python合并 → Schema与语义校验 → JSON/Markdown原子写入
       ↓
  立即发布现有高质量HTML（含可收缩目录）
       ├─ 异步PDF
       ├─ 可重试Notion
       └─ 异步独立评估
```

关键路径由 `A + B + C` 的串行和改为三者的最大值。

本地 JSON/Markdown 继续是真实源；SQLite 只保存任务队列、缓存索引和运行跨度。SQLite WAL 允许读写并发且仍保持单机简单部署，参考 [SQLite WAL 文档](https://sqlite.org/wal.html)。

## FastAPI 与 Go 的取舍

### FastAPI：采用，但只做控制面

适合：

- `POST /runs` 触发任务；
- `GET /runs/{id}` 查看阶段、耗时、缓存命中与错误；
- SSE 推送采集/写稿/图片/PDF 进度；
- 提供新闻流、来源健康、人工验证和日报 HTML；
- 常驻一个异步工作进程，复用 HTTP 客户端与浏览器。

不适合：

- 把完整生成任务塞进 FastAPI `BackgroundTasks`。FastAPI 官方文档提示，重计算任务通常需要独立任务系统，参考 [Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)。

本项目是本地单机工具，第一版不需要 Celery/Redis。一个 FastAPI 控制进程、一个持久异步 worker、SQLite WAL 队列即可。进度可使用官方 [Server-Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/)。

### Go：暂不重写

反对近期重写的原因：

- 可被 Go 加速的本地确定性工作不到 0.1 秒；
- Playwright 官方支持 Node.js、Python、Java 和 .NET，不含 Go，参考 [Supported languages](https://playwright.dev/docs/languages)；
- 重写会增加两套状态模型、Schema、测试和浏览器桥接，风险大于收益。

只有在监控规模持续超过约 1000 来源、Python profiler 显示 CPU/内存成为主瓶颈后，才考虑把纯 HTTP/RSS 抓取器拆成 Go sidecar；浏览器、状态机和报告编译仍不应因此重写。

## 保持网页版效果与零 token 新闻流

当前报纸式 HTML 应继续作为产品主界面。原始新闻卡片展示来源标题、来源摘要、发布时间/采集时间、图片、来源健康和故事簇，不调用模型，因此刷新频率不会消耗 LLM token；网络流量和本机 CPU 开销很小。

会消耗 token 的部分是：

- 中文翻译与重写；
- TL;DR；
- 语义去重或模型重排（现有确定性聚类可保持零 token）；
- 三视角研判与跨视角综合。

推荐“两速界面”：新闻流从快照立即显示；日报定稿在后台补齐模型增强。二者使用同一套报纸视觉、图片卡片和左侧可收缩目录，不把用户等待时间绑定到所有增强任务完成。

## 分阶段实施与验收指标

| 阶段 | 改动 | 目标 |
|---|---|---|
| P0：正确计时与幂等定稿 | monotonic spans、修复结束时间、校验前置、媒体缓存、失败节点续跑 | 暖缓存定稿 < 5 s；冷图片 < 30 s |
| P1：抓取并发 | 常驻快照、HTTP-first 全文、Edge Page 池、失败 TTL | 采集 < 45–90 s；10 篇全文 < 20 s |
| P2：确定性写稿 | 移除工具型子 Agent、Schema 输出、缓存复用、一次修复上限 | 远程/快模型 HTML 2–5 min；完整包 5–8 min |
| P3：本地模型优化 | 两级模型、连续批处理、预计算、强模型只研判 | GPU 3–8 min；CPU 通过预热降低用户等待 |
| P4：控制面 | FastAPI、SQLite WAL、SSE、可取消任务、进度与审计页 | 快照新闻流 < 2 s 可见；日报后台完成 |

验收必须分别记录：

- 首屏新闻流时间；
- 最终 HTML 时间；
- PDF、Notion、评估完成时间；
- 每来源和每 URL 的采集方式/耗时；
- 每模型批次的排队、prefill、decode、输出 token、缓存命中；
- 每图片的缓存、DNS、下载、校验和失败退避；
- 各阶段重试次数、复用节点和关键路径。

不得再用一个“总耗时”混合正常流水线、人工复核、修订、测试和安装。
