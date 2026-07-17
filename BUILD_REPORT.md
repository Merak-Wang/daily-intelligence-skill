# Build and Validation Report

## v0.9.8 — 2026-07-17

- Added an explicit interactive `run-edition --open-verification` path that opens the connected Edge verification frontend immediately after collection only when failed, challenged, or rate-limited pages exist; the default 180-second wait keeps it bounded.
- Refactored automatic and manual verification to share `run_pending_verification()`, including capture, immutable index merge, and run/context adoption. No new runtime script or duplicate browser workflow was introduced.
- Kept Cron/Gateway behavior non-interactive by leaving the flag opt-in, documented the boundary in SKILL/README/runbook/Windows/Wiki, and added regression coverage for automatic invocation and the no-pending no-op.
- Unit and architecture tests: 115 passed; Ruff checks and CLI help smoke passed.

## v0.9.7 — 2026-07-16

- Found the reported sentence stored directly in immutable report JSON rather than injected by Notion rendering: it appears in 151 of 213 briefs in the configured 2026-07-16 morning report and 157 of 217 briefs in the 2026-07-15 evening report.
- Added exact and near-pattern rejection for metadata/body-unread disclaimers used as TL;DR. Access boundaries remain structured internal evidence and no longer qualify as reader-facing summaries.
- Replaying the immutable morning report against `morning-r2.json` now rejects all 151 disclaimer summaries in a single validation pass. Unit and architecture tests: 113 passed; Ruff and Python compilation checks passed.

## v0.9.6 — 2026-07-16

- Audited the actually published evening report in the configured `daily-intelligence` data directory: 268 briefs included 140 fake `【外文】` translations and 140 non-summaries (133 “source X reported” placeholders plus seven “see original link” English abstracts). Only three briefs had full text. The independent evaluation scored it 22/45 but incorrectly accepted it for continuity.
- Required Hermes to call all three brief workers concurrently through one batch-mode `delegate_task`; the model itself now owns natural title translation and evidence-bounded Chinese TL;DR writing. Python and runtime scripts are explicitly limited to deterministic serialization.
- Made empty legacy `brief_plan` contexts refresh before authoring instead of falling back to manually inferred targets, which was the immediate cause of this run's 275 mechanically generated draft briefs.
- Expanded validation to reject bracketed language/source prefixes, link-only/source-only placeholders, and English abstracts disguised by a short Chinese prefix. Replaying the immutable bad report now returns exactly 140 title-translation errors and 140 TL;DR errors.
- Added deterministic continuity gates: a score at or below 22, or at least three critically low evidence/summary/traceability/compliance dimensions, must reject the prior report rather than contaminate the next edition. Replaying the stored 22/45 evaluation now loads zero prior events and analyses even though the old evaluator wrote `accept`.
- Unit and architecture tests: 112 passed; Ruff and Python compilation checks passed.

## v0.9.5 — 2026-07-16

- Audited the blocked evening run against its immutable `evening-r2` index. The draft contained 45 briefs, including two fabricated item IDs, one duplicate, six misplaced briefs, and composite featured events; the old compiler then attempted to invent 182 additional semantic briefs after authoring.
- Added a pre-authoring `brief_plan` with deterministic per-source targets/default item IDs and three balanced batches. The compiler now refuses to invent translations or TL;DR text, drops unknown IDs, deduplicates, relocates content to indexed sections, and reports one concise coverage error per incomplete source.
- Flat importance values no longer block publication; indexed source rank provides the stable tie-breaker. Schema 1.5 featured events now contain exactly one source article, while analyses may cite separate corroborating events together.
- Replaying the unchanged failed draft now keeps 42 valid authored briefs, emits no placeholder/title/TL;DR validation errors, and reduces the remaining work to 25 per-source coverage errors plus five genuine event/analysis errors. The draft itself was not rewritten or published.
- Unit and architecture tests: 107 passed; Ruff and Python compilation checks passed.

## v0.9.3 — 2026-07-15

- Split the model-authoring draft contract from the deterministic final report envelope. The contract now includes the exact seven section IDs, top-level fields, full three-domain analysis object, and a mandatory fast `validate-report` pass before finalization.
- Added compatibility normalization for section mappings plus `information.markets`, `technology.tech_news`, and `technology.oss`; unsupported IDs now fail with an actionable allowed-ID list instead of being silently discarded.
- Corrected cross-source event validation so one event may cite several publishers while its primary source matches the first reference. Featured-event linkage now works across sections, and metadata-only disclosure is appended deterministically when needed.
- Replayed the failed 2026-07-15 evening draft unchanged against its immutable evening-r2 index: validation completed with 0 errors and 4 warnings, then a local save smoke produced JSON and Markdown successfully without remote publication.
- Unit and architecture tests: 99 passed; Ruff checks passed.

## v0.9.2 — 2026-07-15

- Fixed the queue clipping reported in a small Edge window by making the document a fixed-height flex column and the complete source list an independent `overflow-y: scroll` region with a visible 14-pixel scrollbar, bottom padding, and a rendered item count.
- Added explicit `rate_limited` state and detection for HTTP 429 plus “temporarily limited/restricted” pages. Reuters restrictions now end that page's verification loop instead of repeatedly probing it.
- Unit and architecture tests: 93 passed; Ruff and Python compilation checks passed. File-URL automation is blocked by Playwright CLI policy, so the generated HTML is validated structurally and the final visual behavior must be observed in the Edge instance launched by `verify-pending`.

## v0.9.1 — 2026-07-15

- Corrected Hugging Face Papers to `https://huggingface.co/papers`; immutable legacy indexes are left untouched while new verification queues rewrite the old `/papers/month` link.
- Replaced the ambiguous static verification page with a collector-aware local frontend. Direct file viewing displays “未连接采集器”; `verify-pending` marks the page connected and pushes per-source progress as the user opens, verifies, and captures pages.
- Unit and architecture tests: 89 passed; Ruff and Python compilation checks passed.

The local file URL cannot be visually automated by the Codex in-app browser security policy, so layout acceptance relies on deterministic HTML assertions and must be visually confirmed in the Edge instance launched by `verify-pending`.

## v0.9.0 — 2026-07-15

- Package wheel: `daily-intelligence-skill 0.9.0` built successfully.
- Unit and architecture tests: 87 passed; Ruff lint/import checks passed. New coverage includes source-rank preservation, target auto-fill, future/old `NEW` downgrade, failed-link queue generation, legacy `no_items + HTTP 403` repair, published-run revision recovery, one-shot evaluator scheduling, and scheduler crash recovery.
- Real morning-run audit: the index contained 614 candidates but the report exposed only 50 briefs; Weibo had 50 indexed items but 2 briefs, BBC 80 but 1, and Hacker News 30 but 3. All 614 items remained `not_fetched`, confirming that the shortage came from report selection and no enrichment occurred.
- Deterministic report compiler: fills missing source targets without a score cutoff, preserves source TopN, removes duplicate briefs, owns IDs/references/counts/score breakdowns/access/freshness states, removes legacy inline evaluation, and leaves only semantic authoring errors to the Agent. The 2026-07-15 draft now reduces from repeated schema failures to one actionable error: two missing analysis domains.
- Reader output: numeric importance and access labels are retained in local JSON but hidden from Markdown/Notion; original-language source titles are allowed, while TL;DR and user-facing explanation remain Chinese.
- Analysis contract: judgement is split into independent geopolitical, AI research/development, and stock-analysis sections.
- Edge workflow: one local queue aggregates failed and challenged links; clicked authenticated Edge tabs are captured immediately into a new index, and a previously published run is reopened as a traceable report revision. The 2026-07-15 queue contains nine links after correctly recovering SEC's legacy `HTTP 403` record from `no_items`.
- Independent evaluation: successful publication creates a hash-bound one-shot Hermes task and returns immediately. Windows Gateway was started and installed through the non-admin Startup-folder fallback, so scheduled jobs can fire after login.
- Hermes installation: synchronized to `%LOCALAPPDATA%\hermes\skills\research\daily-intelligence`; package and skill versions are 0.9.0. Recurring 06:00 morning and 18:00 evening jobs are active against the existing `daily-intel-data` history, with the Gateway running; evaluation remains publication-triggered rather than a fixed-time job.

Not yet proven: a complete live Hermes generation after v0.9.0 has not been timed, and no duplicate paid model run was launched merely to test the scheduler. The next real morning/evening publication will provide the end-to-end timing and automatic-evaluation proof. The local report and evaluation artifacts remain retryable if Notion or the Gateway is temporarily unavailable.

## v0.8.1 — 2026-07-14

- Package wheel: `daily-intelligence-skill 0.8.1` built successfully.
- Unit and architecture tests: 77 passed; Ruff lint/import checks passed.
- Enrichment budget: full-text hard cap reduced from 40 to 12; caller ID order is now the deterministic importance order, and the run records requested/accepted counts without claiming skipped IDs were fetched.
- Enrichment concurrency: Playwright content extraction now uses bounded async pages (default three globally, one per domain) so same-domain waiters do not block unrelated sources.
- Report workload: schema 1.5 now rejects more than 12 featured events; ordinary stories remain lightweight briefs and are not analyzed item by item. Brief drafting may be split into at most three source batches, while the main generator alone merges and writes judgement.
- Real Microsoft Edge smoke: async extraction from a controlled page produced `full_text`, saved 2,500 characters atomically, and closed the persistent Edge context successfully.
- Installed Hermes skill: synchronized to `%LOCALAPPDATA%\hermes\skills\research\daily-intelligence`; package and skill versions are 0.8.1, Hermes reports the skill enabled, configured cap is 12, and repository/installed `content.py` hashes match.
- The prior one-hour bottlenecks are structurally reduced: independent evaluation no longer blocks publication (v0.8.0), ordinary briefs avoid full event/analysis authoring, deterministic fields avoid manual schema repair, and v0.8.1 bounds and parallelizes enrichment.

Not yet proven: a complete live Hermes morning/evening generation has not been timed after this revision, so 10 minutes remains an enforced workflow target rather than a measured guarantee. Network latency, source challenges, and model latency can still produce `completed_partial`.

## v0.8.0 — 2026-07-14

- Package wheel: `daily-intelligence-skill 0.8.0` built successfully.
- Unit and architecture tests: 73 passed.
- Ruff lint/import checks: passed.
- Schema 1.5 coverage: brief/featured-event linkage, deterministic counters/source metrics, NEW timestamp enforcement, report/index URL/title/source/access identity, and standalone post-save validation.
- Post-publication evaluation: report ID/hash binding, immutable evaluation artifact, delayed continuity-state update, run evaluation status update, and Notion block rendering are covered by tests.
- Source collection: per-source targets, 15-item hard cap, cumulative batch enrichment, and balanced multi-page round-robin merge are covered by tests.
- Edge verification: verified pages are extracted from the current authenticated tab without a second navigation.
- Native Microsoft Edge CLI smoke: system Edge opened `example.com`, produced a DOM snapshot, and closed successfully.
- Live BBC smoke: six configured pages completed successfully and returned 80 items; a focused follow-up confirmed clean heading extraction and relative-time parsing (5 of the first 10 cards exposed parseable timestamps; undated cards remain ineligible for `NEW`).
- Installed Hermes skill: synchronized to `%LOCALAPPDATA%\hermes\skills\research\daily-intelligence`, package version 0.8.0, Hermes status enabled, and repository/installed-file SHA-256 comparison reported zero mismatches.
- Runtime drift: the two Hermes-generated files previously added under installed `references/` were removed; retrospectives are now instructed to use runtime data only.
- Legacy compatibility: schema 1.1—1.4 and root/nested source-index shapes remain covered.

Not executed: authenticated Notion publication/append or a real Hermes independent evaluator model call. Those operations require the user's active Notion credentials and Hermes provider session. Their deterministic validation, persistence, retry, rendering, and state paths are covered by tests.
