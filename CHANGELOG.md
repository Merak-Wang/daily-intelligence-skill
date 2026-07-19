# Changelog

## Unreleased

- Made manual Edge verification opt-in so `run-edition` no longer opens or waits for the verification queue unless `--open-verification` is passed explicitly; `verify-pending` remains the recommended manual entry point.

## 1.0.0 - 2026-07-18

- First stable release of the twice-daily Hermes intelligence workflow, with local-first delivery, resumable state, evidence boundaries, independent evaluation, and backward-compatible legacy index/schema reads.
- Reworked the GitHub README around a concise value proposition, quick start, output contract, architecture, operational boundaries, and links into the detailed Chinese Wiki.
- Added responsive, safely escaped local HTML reports and a chronological `reports/index.html` archive that work without Notion.
- Added A4 PDF projection from the same HTML through Microsoft Edge, with blocked network requests, page numbering, clickable links, and a ReportLab fallback.
- Made local JSON/Markdown/HTML/PDF delivery the default and kept `--publish` as the explicit opt-in for Notion only.
- Decoupled the independent evaluator from Notion: every successfully saved local report schedules evaluation, which refreshes the HTML/PDF assessment section without mutating the report JSON, Markdown, or content hash.
- Added a local feedback form that downloads JSON without uploading data, output configuration validation, PDF/HTML security and rendering tests, and updated the Chinese Wiki.

## 0.10.0 - 2026-07-17

- Locked every Hermes run to one canonical data root and rejected cross-root run, index, content, report, and evaluation artifacts with an explicit adoption command for migrations.
- Preserved successful enrichment IDs through finalization and added evidence binding checks for source mentions plus an explicit basis requirement for numeric scenarios.
- Added post-evaluation semantic brief reuse keyed by a content fingerprint; changed or poorly evaluated material is re-authored rather than silently reused.
- Added bounded, no-script HTTP index prefetch with global/per-domain limits and sequential Edge fallback for login, challenge, JavaScript, and specialized adapters; rate-limited sources are not hammered again.
- Made the Edge verification frontend automatic for interactive runs and added `--unattended` for Cron/Gateway use. Publication still returns before the isolated evaluator runs.
- Added real phase durations and collection counts to run manifests, split verification out of the CLI, removed browser debug artifacts, and added cross-platform GitHub CI.

## 0.9.8 - 2026-07-17

- Added `run-edition --open-verification` so interactive Hermes Desktop runs automatically open the connected Edge verification frontend after collection when failed, challenged, or rate-limited pages exist.
- Reused the same verification-and-index-adoption implementation for automatic and manual `verify-pending` flows; no new runtime script was added.
- Kept scheduled Cron/Gateway runs non-interactive by requiring them to omit the new flag, and bounded the interactive default wait to 180 seconds.

## 0.9.7 - 2026-07-16

- Rejected the legacy metadata disclaimer “仅取得来源标题或公开元数据，正文尚未读取；请通过原文链接查看完整内容” and close variants when used as TL;DR text.
- Kept access boundaries in structured `source_ref.access` or internal evidence notes instead of rendering them as reader-facing summaries.

## 0.9.6 - 2026-07-16

- Required one batch-mode Hermes `delegate_task` call so all three brief batches use model-authored translation and summarization instead of runtime scripts or string templates.
- Made an empty/missing `brief_plan` a context-refresh condition rather than a fallback to manually inferred source targets.
- Rejected `【外文】`/source prefixes, “see original link”, “source X reported”, and English abstracts disguised with a short Chinese prefix.
- Defined the TL;DR evidence hierarchy as fetched `content_path`, public description/abstract, then a strictly title-bounded Chinese restatement.
- Required one canonical runtime data directory per task to prevent manual and scheduled reports from splitting continuity state.

## 0.9.5 - 2026-07-16

- Added a machine-readable per-source `brief_plan` before authoring so three brief workers have deterministic coverage targets and exact default item IDs.
- Stopped the compiler from inventing missing briefs, Chinese translations, or TL;DR text; coverage gaps now produce one actionable error per source.
- Dropped unknown item IDs, moved misclassified briefs/events to their indexed sections, and made original source rank a deterministic non-blocking tie-breaker.
- Required schema 1.5 featured events to contain exactly one source article; corroborating articles remain separate events that analysis can cite together.

## 0.9.4 - 2026-07-16

- Preserved non-Chinese source headlines verbatim, added a separate Chinese `title_zh` line, and blocked `[英]` markers, headline-only summaries, workflow placeholders, and unread-body claims that contradict fetched content.
- Prioritized enriched items in authoring context and added three balanced source batches for parallel brief writing without duplicating candidate payloads.
- Rejected multiple articles from the same publisher inside one featured event and required cross-publisher references to corroborate the same event.
- Added a dedicated arXiv list adapter, low-information navigation filtering, Anthropic team-page and GitHub trending-navigation filters, and safer TWZ card/date/description extraction.
- Changed post-publication evaluation from a fragile one-shot job to three bounded asynchronous attempts for transient model/API connection failures.

## 0.9.3 - 2026-07-15

- Added an exact model-authoring draft contract with canonical section IDs, complete analysis fields, and a mandatory fast `validate-report` step before finalization.
- Normalized legacy section mappings and aliases without silently dropping their content.
- Fixed cross-source featured events so the primary source only has to match the first evidence reference.
- Linked featured events to matching briefs across sections, appended deterministic metadata-only disclosures, and ignored brief-only analysis references with actionable warnings.

## 0.9.2 - 2026-07-15

- Rebuilt the verification page as a full-height flex layout with a dedicated always-scrollable source list, wide scrollbar, compact header, and explicit total/processed counts.
- Added typed `rate_limited` source status for HTTP 429 and temporary-access messages such as Reuters restrictions; interactive verification now stops retrying those pages and retains their links for a later edition.

## 0.9.1 - 2026-07-15

- Changed Hugging Face Papers to the stable `https://huggingface.co/papers` page and transparently rewrote legacy `/papers/month` verification links.
- Upgraded the Edge verification queue into a collector-aware local frontend with connected/offline state and per-source waiting, verification, captured, and extraction-failure feedback.

## 0.9.0 - 2026-07-15

- Filled per-source coverage targets without an importance cutoff while preserving original source rank and allowing previously unreported older items.
- Added a deterministic report compiler for IDs, source snapshots, counts, score breakdowns, freshness status, confidence caps, and pending-source links.
- Split judgement into independent geopolitical, AI research/development, and stock-analysis sections.
- Hid numeric importance and content-access labels from reader-facing Markdown and Notion while retaining them in the local JSON truth.
- Reworked Edge verification into one failed-link queue that captures structured items from user-opened authenticated tabs and prepares a report revision.
- Automatically scheduled a hash-bound one-shot independent evaluation after successful publication instead of waiting in the generation path.
- Increased default source coverage, retained the 15-item hard cap, and kept full-text enrichment limited to at most 12 analysis-critical items with bounded concurrency.

## 0.8.1 - 2026-07-14

- Reduced the per-edition full-text hard cap from 40 to 12 and preserved caller order as enrichment priority.
- Reworked full-text extraction to use bounded async browser pages: three globally by default and one per domain.
- Capped schema 1.5 featured events at 12; ordinary stories remain lightweight briefs and are not analyzed item by item.
- Recorded enrichment request, acceptance, cap, and concurrency settings in the run manifest.

## 0.8.0 - 2026-07-14

- Added schema 1.5 with lightweight `briefs[]` for broad coverage and selected `items[]` for evidence-heavy continuity and judgement.
- Added configurable per-source report targets, retained the hard 15-item cap, and balanced multi-page source merging so later sections are not starved.
- Added generic publication-date extraction and made missing/stale dates block `NEW` in schema 1.5.
- Added deterministic report normalization, explicit source metrics, and strict report/index URL, title, source, and access identity checks.
- Made full-text selection cumulative and batch-oriented across repeated enrich calls.
- Moved independent evaluation after publication into hash-bound immutable evaluation artifacts with retryable Notion append.
- Delayed long-term continuity-state updates until independent evaluation while allowing the report itself to publish immediately.
- Updated Hermes procedures, report contract, runbook, system design, README, and Chinese Repo Wiki for the new workflow.

## 0.7.0 - 2026-07-14

- Added schema 1.4 source-grouped rendering with a strict 15-item per-source cap.
- Added multi-page source exploration, Guardian UK coverage, persistent same-domain dynamic pages, and the Papers with Code successor route.
- Made visible Edge verification capture successful authenticated pages immediately while preserving failed links.
- Added compact budgeted context, history-contamination controls, and Notion user-feedback ingestion.
- Added multi-perspective narrative judgement and a separate nine-dimension evaluation contract.
- Upgraded Markdown and Notion layouts with source headings, numbered items, callouts, toggles, tables, and optional public images.
- Removed obsolete run wrappers, duplicate legacy design documents, and unused revision-copy code.
- Fixed stable wheel installs so the CLI locates configs and schemas from the active Hermes skill directory.

## 0.6.0 - 2026-07-14

- Fixed the published hierarchy to 资讯、技术、研判 with seven always-present subsections.
- Added schema 1.3 judgement coverage and evening change/next-day-watch validation.
- Moved market sources to `information.market` and technical news to `technology.news`.
- Made Microsoft Edge the native Windows default with a dedicated persistent login profile.
- Strengthened on-demand body loading and metadata-only disclosure rules.

## 0.5.0 - 2026-07-13

- Added `information.technology` while retaining technical-community news under `technology.news`.
- Made interactive verification visible and non-interactive-terminal safe with automatic timeout.
- Added publication-age freshness caps and continuity-aware `NEW` validation.
- Added TWZ card-date extraction and Yahoo comment-title filtering.
- Added publication timestamps to Markdown and Notion evidence links.

## 0.3.0 - 2026-07-11

- Added fixed information/technology/analysis taxonomy and adapter registry.
- Added immutable index, context, content, report, and state-history revisions.
- Added run state machine with locks and prepare/enrich/finalize workflow.
- Added complete JSON/Markdown persistence and continuity state updates.
- Added explainable importance scoring and stronger evidence validation.
- Added recoverable Notion publication with complete analysis output.
- Expanded architecture tests to 19 passing tests.

## 0.1.0 — 2026-07-11

- Added Hermes-compatible `SKILL.md` with progressive-disclosure references.
- Added Playwright source collection, challenge detection, and dedicated persistent profile support.
- Added legacy JSON importer and source-specific article filters.
- Added on-demand article-body extraction and compact continuity bundles.
- Added structured report contract, validator, and Notion publisher.
- Added unit tests and sample inputs.
