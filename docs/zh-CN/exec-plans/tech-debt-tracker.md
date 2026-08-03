# 技术债追踪器

**状态：** 已验证
**负责人：** 仓库维护者
**最后验证：** 2026-08-02

| ID | 优先级 | 差距 | 证据 | 下一步 |
| --- | --- | --- | --- | --- |
| TD-001 | 高 | `skills/signaltrail/` 是已检入且已与根级事实源漂移的发布快照。 | 实现、references、configs 和 templates 的文件 Hash 不同。 | 决定删除它或仅在发布时重建；决策前保留用户当前改动。 |
| TD-002 | 中 | 校验和渲染集中在少数超长函数中。 | `validate_report_data`、`compile_report_data`、`render_report_markdown` 和 `report_to_blocks` 最大。 | 先补刻画测试，再按规则/渲染族拆分，不改变 Schema 或输出。 |
| TD-003 | 中 | 详细 `references/` 主要为中文，运行入口主要为英文。 | 记录有用，但语言权威性是隐式的。 | 只在触及时翻译；保持英文工程摘要，不重复运行策略。 |
| TD-004 | 低 | CLI 已统一 JSON 输出/读取，但命令分派仍然很大。 | `main()` 仍拥有许多独立命令。 | 先补命令级刻画测试，再迁移到类型化处理器。 |

2026-08-02 审计已解决：

- 用注入时钟替代依赖真实日期的 Monitor fixture。
- 统一 JSON、Text、Bytes 原子写入，并使用无冲突临时文件名。
- 保证不可变 JSON 在并发创建时也绝不覆盖。
- 集中类型化 JSON object 读取和 CLI JSON 输出。
- 为每个维护中的 Python 定义补充语义化中文“处理/输入/输出”契约：输入说明来源和消费字段，
  输出说明对下游的意义；关键边界增加行内理由，并用 AST 门禁拒绝空洞模板措辞。

英文权威版本见 [`tech-debt-tracker.md`](../../exec-plans/tech-debt-tracker.md)。
