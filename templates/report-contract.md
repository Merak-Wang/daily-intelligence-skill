# 结构化日报契约（schema 1.5）

输出 UTF-8 JSON。用户可见文字使用简体中文；来源原题、URL、论文/项目名和技术术语可保留原文。Python 强制设置 schema/language/时间，生成报告、事件和分析 ID，并从索引补齐引用身份、access、来源排名、状态、计数和 `evaluation_status`。不要手工复制这些字段。

## 固定结构

报告按以下次序渲染：

1. 资讯：国际、国内新闻、军事、市场。
2. 技术：技术新闻、值得阅读的论文、今日值得关注的开源项目。
3. 研判。
4. 质量评估与用户反馈（初次发布显示评估待补充）。

七个 section 由 Python 补齐并排序。渲染器按 brief 来源形成三级标题；成功来源有足够真实候选时必须达到 `report_target`，每来源不超过 `report_max`（全局硬上限 15），并按内部 `importance` 降序；重要性相同时按索引 `source_rank` 保留来源顺序。不得设置固定分数淘汰线。写作前先合并 context 的 `reusable_briefs`，再逐项完成 `brief_plan.author_item_ids`；`target_count` 是本来源最低覆盖数，`default_item_ids` 是确定性基线，只能替换为同一来源候选。Python 不会用模板生成中文标题或 TL;DR；它只可复用内容指纹一致、独立评估已批准的旧 brief。不足时返回按来源汇总的覆盖错误。

## Agent 草稿输入

Agent 写的是精简草稿，不是最终发布 JSON。必须使用数组形式的 `sections` 和 `analyses`，并使用下列精确 section ID；不要手写 `report_id`、`revision`、`generated_at`、来源身份、access、计数、评分分解或最终事件 ID。

```json
{
  "schema_version": "1.5",
  "date": "2026-07-15",
  "edition": "evening",
  "title": "每日情报晚报 — 2026年7月15日",
  "executive_summary": ["中文摘要一。", "中文摘要二。"],
  "changes": ["晚报相对晨报的新增事实或判断修正。"],
  "tomorrow_watch_items": ["次日需要确认的信号。"],
  "sections": [
    {"id": "information.international", "briefs": [], "items": []},
    {"id": "information.domestic", "briefs": [], "items": []},
    {"id": "information.military", "briefs": [], "items": []},
    {"id": "information.market", "briefs": [], "items": []},
    {"id": "technology.news", "briefs": [], "items": []},
    {"id": "technology.papers", "briefs": [], "items": []},
    {"id": "technology.open_source", "briefs": [], "items": []}
  ],
  "analyses": []
}
```

不要输出 `information.markets`、`technology.tech_news` 或 `technology.oss`。Python 为旧草稿兼容这些别名，但新草稿必须使用规范 ID。晨报的 `changes` 和 `tomorrow_watch_items` 可为空；晚报两者都必须有中文内容。`executive_summary` 始终是字符串数组，不是单个字符串。

## 两层内容

`briefs[]` 是日报覆盖层。Agent 只需填写语义字段；Python 依据 `item_id` 从索引补齐原题、URL、access、发布时间和来源身份：

```json
{
  "item_id": "hacker_news-abc123",
  "title": "Original source headline",
  "title_zh": "原文标题的中文翻译",
  "tldr": "忠于已读取正文或公开摘要的中文摘要。",
  "importance": 78,
  "status": "NEW",
  "evidence_note": "未读取正文，仅依据公开摘要；未补写摘要外事实。"
}
```

`title` 最终由 Python 覆盖为索引中的原题。原题已有中文时不要写 `title_zh`；原题不是中文时，Hermes 模型必须填写自然、完整的中文 `title_zh`。不要添加 `[英]`、`[EN]`、`【外文】`、来源名或截断英文。TL;DR 不得是“来源 X 报道”“详见原文链接”“仅取得来源标题或公开元数据，正文尚未读取”、英文摘要前加中文前缀、标题重复或其他占位文案。若索引已有 `full_text/partial`，读取 `content_path` 后总结；否则根据公开 `description`/摘要翻译并压缩；只有标题时，仅把标题明确表达的事实忠实改写成简短中文句子，不得添加标题外事实。访问状态只保存在 `source_ref.access` 或内部 `evidence_note`，不进入 TL;DR。

`featured_event_id`、`source_ref`、`primary_source` 和来源排名由 Python 补齐。`items[]` 是证据与连续性层，通常 6—10 条、硬上限 12 条；普通 brief 不需要逐条研判。精选事件草稿只引用索引 item ID：

```json
{
  "title": "中文事件标题",
  "tldr": "经证据校验的摘要。",
  "why_it_matters": "具体影响。",
  "importance": 82,
  "importance_reason": "评分依据。",
  "confidence": 0.7,
  "status": "NEW",
  "source_item_ids": ["hacker_news-abc123"],
  "evidence_notes": [],
  "tags": []
}
```

Python 会把缺少有效当日/昨日发布时间的 `NEW` 改为 `WATCH`，并按 access 限制置信度。正文未读时只能复述已观察到的标题/公开摘要。旧闻如果近期从未展示可以作为 brief 入选，但时效性评估不把它计作当日新闻。

schema 1.5 中每个精选事件的 `source_item_ids` 必须恰好包含一篇来源文章。另一媒体的交叉证据应写成独立精选事件，研判通过 `evidence_item_ids` 同时引用两者。这样能避免把主题相近但事实无关的文章合并成一个事件。不要把只存在于 `briefs[]` 的 item ID 用于研判；研判引用的每个 `evidence_item_ids` 都必须至少出现在一个精选事件的 `source_item_ids` 中。

## 研判

必须分别输出三个 analysis domain：`geopolitics`、`ai_technology`、`markets`，对应“从地缘政治专家的角度”“从 AI 研究/开发工程师的角度”“从股票分析师的角度”。每个使用 `facts`、`reasoning`、`counter_evidence`、`scenarios`、`implications`、`actions`、`watch_signals`、`invalidation_signals`，并用 `evidence_item_ids` 引用索引 item ID；Python 转为事件 ID。

每个 analysis 使用以下完整草稿结构；三个 domain 各输出一个对象：

```json
{
  "domain": "geopolitics",
  "claim": "中文核心判断。",
  "confidence": 0.7,
  "state_change": "new",
  "facts": ["已读取来源能够直接支持的事实。"],
  "reasoning": "从事实到判断的中文推导。",
  "counter_evidence": ["反证、不确定性或不同解释。"],
  "scenarios": ["后续可能情景及条件。"],
  "scenario_basis": "仅在情景含概率、价格或数字区间时填写：说明来源，或明确这是用于压力测试的假设。",
  "implications": ["对相关主体的影响。"],
  "actions": ["可执行的观察、学习或研究建议。"],
  "watch_signals": ["需要持续观察的信号。"],
  "invalidation_signals": ["哪些新事实会推翻该判断。"],
  "narrative": "可独立阅读的中文研判正文，具体关联当日事件。",
  "historical_context": "必要的历史背景及其与当日事实的关系。",
  "dialectical_analysis": "主要矛盾、次要矛盾、推动因素与制约因素。",
  "stakeholder_positions": [
    {"stakeholder": "相关主体", "interests": "核心利益", "position": "立场与可能行动"}
  ],
  "evidence_item_ids": ["必须已被精选事件引用的索引 item ID"]
}
```

`state_change` 只能是 `new`、`strengthening`、`unchanged`、`weakening`、`revised`、`invalidated`、`closed`。Python 根据 domain 补齐 `perspectives`、`assessment_types` 和 `analysis_id`。

- `perspectives`：`geopolitics`、`ai_research_engineering`、`equity_analysis`、`china_standpoint`、`western_standpoint`。
- `assessment_types`：`trend`、`risk`、`learning_research`。
- `narrative`、`historical_context`、`dialectical_analysis`、`stakeholder_positions`。

三个部分整体覆盖至少 60% 的精选事件；不要求覆盖全部 briefs。中国/西方立场放入相关部分的 `stakeholder_positions`。观点必须能追溯到事实或明确推导，不得把推断写成事实。

精选事件的 `evidence_notes` 或研判的 `facts` 如果点名 BBC、CNBC、路透等来源，该来源必须能从对应 `source_item_ids` / `evidence_item_ids` 追溯到，不能用未绑定来源增强措辞。`scenarios` 中若出现百分比、概率、价格、金额或数字区间，必须填写中文 `scenario_basis`，明确数据来源或说明它只是情景假设；不能把模型自行给出的数字包装成预测事实。

晚报在同一日期页面补充日间新增、事实确认、判断修正和至少一项次日观察；`changes` 和 `tomorrow_watch_items` 不能留空。

保存或发布前先运行快速内存编译与校验；只有 `errors` 为 0 才调用 `finalize-edition`：

```text
daily-intel validate-report DRAFT.json --index INDEX.json
```

该命令不写报告、不分配 revision、不发布。不要使用 `finalize-edition` 充当格式检查器。

## 发布后独立评估

报告草稿不要包含 `quality_evaluation`；即使误写，Python 也会移除。发布后一次性隔离 Agent 自动输出单独 JSON：

```json
{
  "evaluator_role": "independent",
  "evaluated_report_id": "daily-2026-07-14-morning-r1",
  "evaluated_content_hash": "由 run artifact 提供的 SHA-256",
  "dimensions": [
    {"id": "coverage", "score": 4, "finding": "中文、具体、简洁的结论。"}
  ],
  "total_score": 36,
  "main_defects": [],
  "insufficient_evidence": [],
  "improvements": [],
  "continuity_decision": "accept",
  "exclude_from_continuity": []
}
```

`dimensions` 必须完整包含九项：`coverage`、`importance_ordering`、`factual_reliability`、`summary_accuracy`、`analysis_traceability`、`historical_continuity`、`readability`、`timeliness`、`compliance_boundaries`。每项 1—5 分，总分必须等于九项之和。总分不高于 22，或四个关键维度（事实可靠性、摘要准确性、分析可追溯性、合规边界）中至少三项不高于 2 分时必须 `reject` 并排除 `all`；`accept` 要求总分至少 32 且关键维度均高于 2。`selective` 必须给出明确排除项。
