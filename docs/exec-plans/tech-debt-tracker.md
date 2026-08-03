# Technical-Debt Tracker

**Status:** Verified
**Owner:** Repository maintainers
**Last verified:** 2026-08-02

| ID | Priority | Gap | Evidence | Next action |
| --- | --- | --- | --- | --- |
| TD-001 | High | `skills/signaltrail/` is a checked-in release snapshot that has drifted from canonical root sources. | File hashes differ across implementation, references, configs, and templates. | Decide whether to delete it or regenerate it only at release time; preserve current user edits until that decision. |
| TD-002 | Medium | Validation and rendering are concentrated in very long functions. | `validate_report_data`, `compile_report_data`, `render_report_markdown`, and `report_to_blocks` are the largest functions. | Add characterization tests, then extract rule/render families without changing schemas or output. |
| TD-003 | Medium | Detailed `references/` are primarily Chinese while runtime entry documents are English. | The records are useful but language authority is implicit. | Translate only when touched; keep English engineering summaries and do not duplicate runtime policy. |
| TD-004 | Low | CLI command dispatch remains large despite shared JSON output/read helpers. | `main()` still owns many independent commands. | Move cohesive command families to typed handlers after command-level characterization tests exist. |

Resolved in the 2026-08-02 audit:

- Replaced the wall-clock-dependent monitor fixture with an injected clock.
- Unified JSON, text, and byte atomic writers with collision-safe temporary names.
- Made immutable JSON creation no-overwrite even under concurrent writers.
- Centralized typed JSON-object reads and repeated CLI JSON output.
- Added semantic Chinese logic/input/output contracts to every maintained Python definition.
  Inputs name provenance and consumed fields; outputs explain downstream meaning. Critical
  boundaries carry inline rationale, and an AST gate rejects empty template wording.
