# Technical-Debt Tracker

**Status:** Verified
**Owner:** Repository maintainers
**Last verified:** 2026-08-05

| ID | Priority | Gap | Evidence | Next action |
| --- | --- | --- | --- | --- |
| TD-001 | High | The tracked <code>skills/signaltrail/</code> release snapshot has drifted from canonical root sources. | File hashes differ across implementation, references, configs, and templates. The active Hermes installation was separately migrated and now matches all 31 canonical Python modules. | Rebuild the tracked snapshot only during an explicit release; do not hand-edit it or mix release regeneration into ordinary implementation work. |
| TD-002 | Medium | Validation and rendering are concentrated in very long functions. | `validate_report_data`, `compile_report_data`, `render_report_markdown`, and `report_to_blocks` are the largest functions. | Add characterization tests, then extract rule/render families without changing schemas or output. |
| TD-003 | Medium | Detailed `references/` are primarily Chinese while runtime entry documents are English. | The records are useful but language authority is implicit. | Translate only when touched; keep English engineering summaries and do not duplicate runtime policy. |
| TD-004 | Low | CLI command dispatch remains large despite shared JSON output/read helpers. | `main()` still owns many independent commands. | Move cohesive command families to typed handlers after command-level characterization tests exist. |
| TD-005 | Medium | The zero-model monitor labels core sources with specialized adapters such as Weibo as `unsupported`, even though the formal edition collector can fetch them. | The 2026-08-05 monitor showed `weibo_hot` as unsupported while the formal index recorded `success` with 50 items; `pboc_monetary_reports` and `bytedance_seed_papers` share the boundary. | Add a neutral `collector_only` monitor capability/status (or an equivalent explicit capability field), keep it out of failure-rate counters, and label it as “edition collector only” without invoking browser/specialized collection from the monitor. |

| TD-006 | High | A process-wide enrichment failure can strand a run in <code>extracting_content</code>, which no normal entry point resumes. | <code>enrich_edition</code> persists the state before an unguarded <code>extract_content</code> call, while ordinary preparation returns an existing non-terminal run unchanged. | Make enrichment checkpointed and re-entrant, preserve index lineage, and add failure-injection tests after the state transition and during immutable index creation. |
| TD-007 | High | Authoring packets are mutable and are not cryptographically bound to the immutable context or authoring session. | Packets use overwrite-capable writes; the session hashes only the main context, and submission validates against the packet currently on disk. | Create packets immutably, persist each packet hash and authorized item IDs in the context/session, and verify them during begin, submit, and recovery. |
| TD-008 | High | Source-cache health and authoring-session lineage can be overstated after state changes. | Cached items can produce formal <code>success</code> while monitor acquisition carried a partial/error state, and enrichment remains allowed after an authoring session has been dispatched. | Preserve acquisition health separately from item availability; reject or explicitly invalidate authoring sessions when enrichment rebuilds their bound context. |
| TD-009 | Medium | Context compaction can omit an explicitly enriched item below the per-source context cap. | <code>_compact_candidates</code> expands the prefix by the count of enriched items rather than the greatest enriched position, so one enriched item below rank 25 can still be excluded. | Union explicitly enriched evidence into the bounded context while preserving immutable Top selection order, and add a rank-26-or-lower regression test. |
| TD-010 | Medium | Report/evaluation derivative persistence lacks a revision transaction and complete idempotency. | JSON can persist without its Markdown peer; concurrent or retried evaluation jobs can overwrite a shared draft and allocate multiple immutable evaluation revisions before projection/state refresh completes. | Add report-revision transaction records, immutable per-attempt evaluation drafts, and one resumable evaluation revision bound to report ID and content hash. |
| TD-011 | Medium | Runtime telemetry and early validation cannot reconstruct several important decisions. | Batch duration starts at the session timestamp; rejected repair attempts are not retained; live acquisition is combined as <code>browser_or_http</code>; analysis-shape errors are mostly deferred to final validation. | Record per-batch dispatch and bounded attempt receipts, separate acquisition-path metrics, and validate the analysis payload before assembly. |
| TD-012 | Medium | Monitor snapshot eligibility and projection-ready milestones are not enforced by one shared truth check. | Preflight checks future time, token use, and structure more strictly than direct monitor loading; HTML failure can still be followed by an unconditional ready milestone. | Share one monitor-snapshot validator and derive readiness milestones only from confirmed artifact existence. |
| TD-013 | Medium | <code>published_at</code> orders only the bounded rows returned by an adapter. | An adapter can truncate its page/feed before the shared sorter sees older/newer rows, so the result is “newest within the fetched subset,” not necessarily newest across the source. | Define adapter acquisition depth for time ordering, expose truncation telemetry, and characterize pagination/window behavior. |
| TD-014 | High | Standalone <code>save-report</code> can compile without the run-owned brief-plan boundary. | The command accepts an index and draft but does not require the context plan; callers outside <code>finalize_edition</code> can therefore bypass exact <code>default_item_ids</code> enforcement. | Require a context/plan artifact or make the standalone command explicitly diagnostic-only; add an out-of-plan rejection test at the CLI boundary. |
| TD-015 | Medium | Verified multi-page source merging lacks sufficient behavior characterization. | Ordering, duplicate replacement, and provenance rules are implemented across capture and merge paths but are not tested for enough multi-page/retry combinations. | Add page-order, duplicate, partial-page, and retry fixtures before refactoring the merge path. |
| TD-016 | Medium | The nominal 45-item authoring batch size is a soft balancing target. | Whole-source grouping can create a packet above 45 items, so downstream output/token limits are not a strict invariant. | Persist an explicit hard packet cap or document/validate the maximum overshoot allowed by whole-source grouping. |
| TD-017 | Medium | Image-heavy Edge PDF projection can become too large for practical distribution. | The final 2026-08-05 morning PDF is about 99 MB while its HTML source is under 1 MB because full-resolution local images are embedded. | Downsample or transcode projection images under a separate PDF budget while preserving the authoritative media records. |
| TD-018 | High | Accepted semantic-cache entries can propagate editorial defects across revisions. | Nine reused briefs in r3 retain internal “packet/data package” wording from earlier accepted reports; content fingerprint and prior acceptance alone do not invalidate this defect class. | Add targeted cache invalidation by item ID/rule, reject pipeline-meta language before cache approval, and re-author affected entries. |
| TD-019 | Medium | Narrative continuity and inline evidence references are not fully machine-bounded. | Structured evidence IDs validate, but prose can still mention a brief outside featured-event evidence; <code>change_from_prior</code> can anchor to a non-adjacent report. | Extract/validate inline item IDs and bind continuity authoring to the immediately preceding eligible report ID and claims. |

Resolved in the 2026-08-02 audit:

- Replaced the wall-clock-dependent monitor fixture with an injected clock.
- Unified JSON, text, and byte atomic writers with collision-safe temporary names.
- Made immutable JSON creation no-overwrite even under concurrent writers.
- Centralized typed JSON-object reads and repeated CLI JSON output.
- Added semantic Chinese logic/input/output contracts to every maintained Python definition.
  Inputs name provenance and consumed fields; outputs explain downstream meaning. Critical
  boundaries carry inline rationale, and an AST gate rejects empty template wording.

Resolved during the 2026-08-05 regeneration:

- Excluded retained monitor-history rows from formal report eligibility.
- Made successful live acquisition authoritative before monitor tail fallback.
- Preserved original source-group positions when merging retried sources.
- Added validation-only report identity without mutating authoring drafts.
- Bound evaluation finalization to canonical source with a shell-neutral Python launcher and
  the absolute canonical report contract.
- Installed the canonical <code>signaltrail</code> runtime, verified all 31 Python modules, and
  removed three legacy <code>daily-intelligence</code> skill copies to the Windows Recycle Bin.
