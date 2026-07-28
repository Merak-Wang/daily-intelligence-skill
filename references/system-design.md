# 系统设计

## 边界

```text
RSS/Atom 条件请求 + 静态 HTML -> 零 token 监控快照 -> 事件聚类/来源健康
-> 核心来源适配器/必要时 Edge -> 不可变候选索引 -> 压缩上下文
-> 并行：受约束 brief 批次 | 图片预取
-> Python 合并 brief -> 紧凑事件 dossier -> 生成 Agent 只写一次研判
-> Python schema 2.0 装配/校验 -> 不可变 JSON/Markdown -> 立即交付 HTML
-> 后台：PDF | 可重试 Notion（可选远程投影）
-> 独立评估 Agent 只评分 -> 独立评估 artifact
-> 刷新 HTML/PDF 评估区 -> 可选追加 Notion -> 受评估约束的长期连续状态
```

Python 拥有状态迁移、revision、访问等级映射、限额、验证和发布；生成 Agent 拥有语义选择、摘要和研判；评估 Agent 只审查已经保存的不可变报告。事实身份校验在发布前完成，主报告不等待主观质量评分；后置评估只给修改和连续性建议，不修改报告。

## 内容模型

资讯固定为国际、国内新闻、军事、市场；技术固定为技术新闻、值得阅读的论文、今日值得关注的开源项目；研判单独渲染。七个内容 section 始终存在。

`briefs[]` 是显示和覆盖单位，负责标题、TL;DR、内部重要性、原始来源排名、链接和可选的确定性 `image` 记录；`items[]` 是精选事件与连续性单位，只承载需要完整证据链或支撑研判的少量事件。这样增加新闻数量不会按比例放大评分、全文读取和研判引用成本。每个有真实候选的来源应尽量达到 `report_target`，不设重要性入选门槛；渲染器按编辑重要性排序，同时显示来源榜单 TopN，每来源最多 15 条。数值评分和正文访问状态留在 JSON，不进入读者版。

研判固定分为“地缘政治专家视角”“AI 研究/开发工程师视角”“股票分析师视角”。schema 2.0 要求三个视角读取同一份 6—10 个精选事件 dossier，分别输出因果链、假设、时间跨度、证据缺口、相对上一版变化和失效条件；最后用跨视角综合显式记录共识、分歧来源、“地缘政治 → 技术 → 市场”传导链与共同观察信号。独立评估控制下一次连续性可接受、选择性排除或完全拒绝的范围。

## 来源发现与验证

来源分成两层：`configs/sources.yaml` 的 32 个核心来源保留日报 `report_target`；`configs/discovery-sources.yaml` 的扩展来源只进入监控、聚类和候选发现，默认 `report_target=0`。来源声明 `tier`、`role`、`bundle`、可选 `feed_urls` 与刷新间隔。新增发现源不会按比例放大摘要、全文读取或研判 token。

监控先读取 RSS/Atom，使用 `ETag`、`Last-Modified`、304、本地解析结果缓存、失败退避与 `Retry-After`。响应必须通过 XML/HTML 嗅探；验证码或 HTML 中间页不得按 feed 解析。来源没有 feed 或 feed 失败时，可自动发现页面声明的 feed，再回退到无脚本 HTML。发布时间缺失的条目保留并标记，只能以采集时间显示，不能参与 NEW/新鲜度。标题词法特征哈希与余弦相似度完成零模型 token 的事件聚类；上一快照的 item 身份用于稳定 story ID。

来源 YAML 声明基础页和静态探索页。Agent 可以通过 CLI 写入 `state/source-pages.json` 增加同域名、高价值的动态栏目页；每来源最多 5 个。动态页是可撤销配置，不改变适配器代码。

一次来源采集可以访问多个栏目页并去重。通用公开索引先用 httpx/Beautiful Soup 做无脚本预取，受全局与同域 semaphore 约束；无条目、登录/挑战、401/403、JavaScript 页面和专用 adapter 才进入顺序 Edge 回退，避免并发操作同一个持久化 profile。正文读取同样使用共享 `httpx.AsyncClient` 并发提取静态正文和元数据，只将仍可能补足正文的 JavaScript 壳页交给 Edge；明确的访问拒绝保留原状态，不用浏览器重撞。已有正文文件直接复用。429 或临时访问限制直接保留为 `rate_limited`。多页结果按轮询合并，避免 BBC/Guardian 的第一个栏目占满上限而饿死后续栏目。部分栏目成功、部分失败时，来源状态是 `partial`，且 `page_results` 保存每页状态和链接。访问失败永远不能静默变成 `no_items`。

`run-edition` 默认不调用手工验证，避免 GUI 等待阻塞生成流程。用户显式运行 `verify-pending` 或传 `--open-verification` 时才启动本地 Edge 队列。队列汇总失败和待验证页面，用户点击链接后，采集器监听新标签并复用当前已登录页面立即提取；只有成功提取到条目才算完成。结果被原子合并到新索引；失败页面继续保留。已发布 run 会进入待修订状态，以新 revision 补充而不是覆盖原报告。同一日期与 edition 的后续 revision 可以复用自身上一 revision 的事件 ID 和来源条目；跨 edition、跨日期或换用另一事件 ID 时仍执行 `NEW` 重复拦截。`--unattended` 保留为默认非交互行为的兼容参数。

## 状态机与文件

```text
created -> collecting -> building_context -> awaiting_selection
-> extracting_content -> awaiting_authoring -> finalizing
-> completed | completed_partial + tail.pending

tail.pending -> tail.running -> tail.completed | tail.partial
tail.partial -> tail.running（只续跑未完成投影）

completed[_partial] -> evaluation pending
-> 独立评估 artifact -> HTML/PDF refresh / [Notion append] / 长期连续状态

机械异常 -> failed；本地 finalization 失败 -> awaiting_authoring
```

```text
data/
  monitor/{snapshot,health,feed-registry}.json
  monitor/feed-cache/<sha256>.json
  indexes/YYYY-MM-DD/<edition>-rN.json
  content/<source>/<item>/<retrieval>.md
  media/image-cache.json
  media/images/<sha-prefix>/<sha256>.<ext>
  context/YYYY-MM-DD/<edition>-rN.json
  context/YYYY-MM-DD/<edition>-rN-authoring/{session,brief-skeleton,analysis-packet,...}.json
  reports/index.html
  reports/YYYY-MM-DD/<edition>-rN.{json,md,html,pdf}
  evaluations/YYYY-MM-DD/<edition>-rN.json
  runs/YYYY-MM-DD/<edition>.json
  state/{events,theses,watchlist,predictions,source-pages,user-feedback}.json
  state/semantic-cache.json
  state/history/<kind>/YYYY-MM-DD-rN.json
  publishing/notion-registry.json
  locks/YYYY-MM-DD-<edition>.lock
```

report JSON/Markdown revision 与内容寻址图片不可覆盖；它们是事实源。报告先完成编译和语义校验，再进入媒体物化；图片 URL 缓存记录内容摘要、验证元数据和失败重试时间，使用共享连接池、全局并发及同域并发限制。默认在 brief 子任务运行时预热图片，因此定稿只读取暖缓存。HTML 是前台交付门槛；PDF、Notion 与独立评估属于可重试 tail，不进入用户等待关键路径。版本化 HTML 使用指向内容寻址媒体的相对路径，桌面 HTML 将已校验图片内嵌为可单独移动的单文件；标题与图片的 DOM 顺序在两种投影中保持一致。PDF 的 Edge 输入使用同一份内嵌图片 HTML 并强制完成图片加载，ReportLab 降级路径直接读取受媒体根约束的已校验文件；两条路径最终都把位图对象写入 PDF。HTML/PDF 是同一 report 的可重建阅读投影，投影失败只记录可操作警告，不撤回已经持久化的 JSON/Markdown，也不分配重复 revision；评估完成后允许原子刷新评估区。Notion 页面只保存元数据并附加便携 HTML；HTML upload ID、内容 hash 和失败信息只写入可重试的发布登记，Notion 仍只是可选远端副本。

## 上下文预算

上下文不嵌入全文，也不重复整个 candidate index。默认每来源最多 25 个紧凑候选；已取得 `full_text/partial` 的候选优先进入上下文，再按来源发布时间和来源原始顺序排列。`report_target` 是有候选时应填满的覆盖目标，`report_max` 是不超过 15 的硬上限。上下文先读取 `state/semantic-cache.json`：只有内容指纹一致且独立评估通过门槛的 brief 进入 `reusable_briefs`；其余 ID 进入 `author_item_ids`。

来源均衡拆成最多 3 个 `brief_authoring_batches`。每个 Hermes 工作单元只读独立 packet 和已列出的正文路径，不浏览、搜索、运行脚本、生成全篇报告或处理其他批次；它只写 packet 指定的 draft path、执行一次提交命令并返回短回执。Python 校验并原子接收各批，原样合并缓存与已接收 brief，再将最多 18 个高价值候选压缩为 analysis packet。主 Agent 不再重读数百条 brief，只选择 6—10 个精选事件并完成一次三视角研判。Python 最后装配完整 schema 2.0 草稿。

authoring session 绑定 run attempt、context 绝对路径与 SHA-256，分发后 context 改变会硬失败，不能混合新旧批次。session 记录 run deadline 减去 120 秒的 `analysis_deadline_at`。截止前缺批次必须等待或修复；截止后才允许确定性降级，coverage override 只能由 session 根据已接收 item 计算，报告草稿不能自行降低目标。每批记录耗时、API、输入/输出 token、模型和退出原因；后端若提供 queue、首 token、prefill、decode 和缓存 token，也以可选字段保留。大段子任务摘要和工具轨迹不进入运行指标。保存报告先写 pending cache，独立评估对事实可靠性、摘要准确性、合规边界和连续性给出合格结果后才提升为 approved；标题、URL、摘要、发布时间、正文状态或正文路径改变都会使指纹失效。每次累计最多读取 12 篇正文；历史报告只转换为稳定 ID、结构化判断和评估诊断。

每个 run 记录自己的绝对 `data_root`。Hermes Home 另存唯一根绑定，所有直接 artifact 命令和状态迁移都先校验路径与 manifest，防止从一套目录读取 enrich 结果、向另一套目录发布日报。`artifacts.enrichment.successful_item_ids` 是正文证据 lineage；finalize 发现这些 ID 在最终 index 中退化或消失会硬失败。

## 兼容性

根级 `items[]` 是规范索引模型。采集与 enrich 同步维护旧 `sources[].items[]`，以兼容既有 Hermes 数据和旧工具。schema 1.1—1.5 仍可读取；新报告使用 2.0。新 context 标记为 2.0 时，发布门禁拒绝 1.5 草稿，避免回退路径绕过跨视角综合。监控快照是新增的独立读取模型，不改变旧索引 JSON 的根级形状。
