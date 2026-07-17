# 系统设计

## 边界

```text
来源适配器 -> 不可变候选索引 -> 压缩上下文
-> 生成 Agent 选择/按需正文/写草稿
-> Python 确定性校验 -> 不可变 JSON/Markdown
-> 可重试 Notion 发布（立即交付）
-> 独立评估 Agent 只评分 -> 独立评估 artifact
-> 追加 Notion 评估 -> 受评估约束的长期连续状态
```

Python 拥有状态迁移、revision、访问等级映射、限额、验证和发布；生成 Agent 拥有语义选择、摘要和研判；评估 Agent 只审查已经保存的不可变报告。事实身份校验在发布前完成，主报告不等待主观质量评分；后置评估只给修改和连续性建议，不修改报告。

## 内容模型

资讯固定为国际、国内新闻、军事、市场；技术固定为技术新闻、值得阅读的论文、今日值得关注的开源项目；研判单独渲染。七个内容 section 始终存在。

`briefs[]` 是显示和覆盖单位，负责标题、TL;DR、内部重要性、原始来源排名和链接；`items[]` 是精选事件与连续性单位，只承载需要完整证据链或支撑研判的少量事件。这样增加新闻数量不会按比例放大评分、全文读取和研判引用成本。每个有真实候选的来源应尽量达到 `report_target`，不设重要性入选门槛；渲染器按编辑重要性排序，同时显示来源榜单 TopN，每来源最多 15 条。数值评分和正文访问状态留在 JSON，不进入读者版。

研判固定分为“地缘政治专家视角”“AI 研究/开发工程师视角”“股票分析师视角”，分别引用精选事件 ID，并纳入历史条件、矛盾关系和不同利益相关方立场。独立评估控制下一次连续性可接受、选择性排除或完全拒绝的范围。

## 来源发现与验证

来源 YAML 声明基础页和静态探索页。Agent 可以通过 CLI 写入 `state/source-pages.json` 增加同域名、高价值的动态栏目页；每来源最多 5 个。动态页是可撤销配置，不改变适配器代码。

一次来源采集可以访问多个栏目页并去重。多页结果按轮询合并，避免 BBC/Guardian 的第一个栏目占满上限而饿死后续科技、商业或科学栏目。部分栏目成功、部分失败时，来源状态是 `partial`，且 `page_results` 保存每页状态和链接。访问失败永远不能静默变成 `no_items`。

交互式 `run-edition --open-verification` 在采集结束后自动调用与 `verify-pending` 相同的本地 Edge 队列实现；前者防止用户忘记验证，后者用于稍后重开。队列汇总失败和待验证页面，用户点击链接后，采集器监听新标签并复用当前已登录页面立即提取；只有成功提取到条目才算完成。结果被原子合并到新索引；失败页面继续保留。已发布 run 会进入待修订状态，以新 revision 补充而不是覆盖原报告。无人值守流程不启用该开关。

## 状态机与文件

```text
created -> collecting -> building_context -> awaiting_selection
-> extracting_content -> awaiting_authoring -> finalizing
-> publishing -> completed | completed_partial

completed[_partial] -> evaluation pending
-> 独立评估 artifact -> Notion append / 长期连续状态

机械异常 -> failed；本地 finalization 失败 -> awaiting_authoring
```

```text
data/
  indexes/YYYY-MM-DD/<edition>-rN.json
  content/<source>/<item>/<retrieval>.md
  context/YYYY-MM-DD/<edition>-rN.json
  reports/YYYY-MM-DD/<edition>-rN.{json,md}
  evaluations/YYYY-MM-DD/<edition>-rN.json
  runs/YYYY-MM-DD/<edition>.json
  state/{events,theses,watchlist,predictions,source-pages,user-feedback}.json
  state/history/<kind>/YYYY-MM-DD-rN.json
  publishing/notion-registry.json
  locks/YYYY-MM-DD-<edition>.lock
```

revision 文件不可覆盖；本地 JSON/Markdown 是事实源。Notion 只保存可重试的远端副本和用户可编辑反馈。

## 上下文预算

上下文不嵌入全文，也不重复整个 candidate index。默认每来源最多 25 个紧凑候选；已取得 `full_text/partial` 的候选优先进入上下文，再按来源发布时间和来源原始顺序排列。`report_target` 是有候选时应填满的覆盖目标，`report_max` 是不超过 15 的硬上限。上下文把来源均衡拆成最多 3 个 `brief_authoring_batches`，并生成机器可读 `brief_plan`，记录每来源的 section、批次、目标数和默认 item ID。三个 Hermes 模型子 Agent 并行拥有语义字段：按 `content_path > description/公开摘要 > 标题明确事实` 的证据层级完成翻译和 TL;DR；Python 只拥有校验、归位、去重与确定性字段。缺失 `brief_plan` 是旧 context，不是可回退状态，必须刷新后再写作。每次累计最多读取 12 篇正文；历史报告只转换为稳定 ID、结构化判断和评估诊断。

## 兼容性

根级 `items[]` 是规范索引模型。采集与 enrich 同步维护旧 `sources[].items[]`，以兼容既有 Hermes 数据和旧工具。schema 1.1—1.4 仍可读取；新报告使用 1.5。
