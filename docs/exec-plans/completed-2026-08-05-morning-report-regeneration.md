# 2026-08-05 Morning Report Regeneration and Observed Data Flow

**Purpose:** Record the complete observed regeneration path, artifact lineage, ranking checks,
code-review findings, cleanup decisions, and verification evidence for the 2026-08-05 morning
edition.

**Status:** Verified
**Owner:** Repository maintainers
**Last verified:** 2026-08-05

Chinese mirror:
[2026-08-05 今日日报重生成与数据流实录](../zh-CN/exec-plans/completed-2026-08-05-morning-report-regeneration.md).

## Outcome

The authoritative result is <code>daily-2026-08-05-morning-r3</code>. It contains 403 ordinary
briefs from 29 represented sources, seven non-empty sections, and eight featured events.
The canonical source/evidence index contains 709 items from 32 configured sources.

The requested ranking rule is satisfied:

- the default remains <code>collection.item_order: source</code>;
- the optional alternative remains <code>published_at</code>;
- ordinary briefs preserve the current index and <code>brief_plan.default_item_ids</code>
  sequence;
- TWZ is shown as <code>来源Top1</code> through <code>来源Top15</code>;
- Weibo is shown as <code>热搜Top1</code> through <code>热搜Top15</code>;
- InfoQ is shown as <code>来源Top1</code> through <code>来源Top15</code>;
- no ordinary source group is re-sorted by internal importance.

The run is <code>completed_partial</code>, not because briefs are missing, but because four
source acquisition states remain explicit: SEC and Reuters require verification, Defence Blog
is partial after returning ten real candidates before an HTTP 403, and Yahoo is rate limited
with HTTP 429. All 403 planned briefs exist.

## Authority and Observation Method

This record distinguishes three kinds of evidence:

- **Observed:** read directly from the immutable index, context, authoring receipts, report,
  run manifest, evaluation artifacts, projection files, or filesystem metadata.
- **Recomputed:** independently derived from the artifacts, such as counts, rank sequences,
  item-ID equality, checksums, and section totals.
- **Reviewed:** traced through canonical root source and focused regression tests.

The canonical implementation is under [src/daily_intelligence](../../src/daily_intelligence/).
Generated or installed copies under <code>build/</code>, <code>dist/</code>,
<code>skills/signaltrail/</code>, and Hermes skill roots were not treated as implementation
authority.

## End-to-End Flow

~~~mermaid
flowchart TD
    A["Attempt 4 manifest<br/>10:58:16"] --> B["Monitor snapshot reused<br/>current rows only"]
    B --> C["32 source collectors<br/>live + eligible monitor fallback"]
    C --> D["Normalize / deduplicate<br/>assign original source_rank"]
    D --> E["Apply item_order<br/>source by default"]
    E --> F["Immutable index r6<br/>709 canonical rows"]
    F --> G["Compact context r6<br/>565 candidates / 29 plans"]
    G --> H["Select 12 enrichment IDs"]
    H --> I["HTTP enrichment<br/>12 attempted / 6 full text"]
    I --> J["Immutable index r7<br/>derived from r6"]
    J --> K["Context r7<br/>403 planned"]
    K --> L["Semantic reuse boundary<br/>386 reused / 17 author"]
    L --> M["One accepted authoring batch<br/>17 briefs"]
    M --> N["Deterministic merge<br/>403 briefs"]
    N --> O["Analysis packet<br/>18 candidates"]
    O --> P["Analysis draft<br/>8 featured events"]
    P --> Q["Compile + validate<br/>0 errors / 0 warnings"]
    Q --> R["Immutable report r3<br/>JSON + Markdown"]
    R --> S["HTML + desktop copy"]
    S --> T["Deferred Edge PDF"]
    T --> U["Independent evaluation<br/>canonical contract"]
    U --> V["Post-evaluation HTML/PDF<br/>Top order rechecked"]
~~~

## Artifact Lineage

| Role | Absolute path | Meaning |
| --- | --- | --- |
| Run manifest | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/runs/2026-08-05/morning.json</code> | Attempt, state, timestamps, budgets, pending sources, and artifact pointers |
| Collection index r6 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/indexes/2026-08-05/morning-r6.json</code> | Attempt-4 collection output before selected-content enrichment |
| Final index r7 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/indexes/2026-08-05/morning-r7.json</code> | Final 709-row evidence index; derived from r6 |
| Final context r7 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/context/2026-08-05/morning-r7.json</code> | Compact candidates, plans, semantic reuse, and authoring work |
| Authoring session | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/context/2026-08-05/morning-r7-authoring/session.json</code> | Attempt, context hash, deadlines, and accepted receipt |
| Analysis packet | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/context/2026-08-05/morning-r7-authoring/analysis-packet.json</code> | Bounded 18-candidate analysis input |
| Analysis draft | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/context/2026-08-05/morning-r7-authoring/analysis-draft.json</code> | Eight-event analytical output |
| Report JSON r3 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/reports/2026-08-05/morning-r3.json</code> | Authoritative report |
| Report Markdown r3 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/reports/2026-08-05/morning-r3.md</code> | Text projection |
| Report HTML r3 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/reports/2026-08-05/morning-r3.html</code> | Local interactive projection |
| Desktop HTML r3 | <code>C:/Users/wmf/Desktop/daily-intelligence-2026-08-05-morning-r3.html</code> | Convenience copy |
| Report PDF r3 | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/reports/2026-08-05/morning-r3.pdf</code> | Portable projection |
| Independent evaluation | <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/evaluations/2026-08-05/morning-r3.json</code> | Corrected evaluation bound to report ID and content hash |

Root <code>items[]</code> in an index is canonical. The per-source
<code>sources[].items[]</code> view is a required compatibility mirror. For r7 both views
contain 709 rows, and the mirror IDs reconcile with the root view.

## Detailed Stage Record

### 1. Run creation and bounded state

[prepare_edition](../../src/daily_intelligence/workflow.py) created attempt 4 at 10:58:16
Asia/Shanghai. The edition window was 2026-08-04 18:00 through 2026-08-05 06:00. Runtime
constraints were persisted before collection:

- 3,600-second maximum runtime;
- 12 selected full-text attempts;
- 15 report items per formal source;
- deadline 11:58:16;
- output language <code>zh-CN</code>;
- data root <code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence</code>.

The final main-run elapsed time was 2,226 seconds and <code>deadline_exceeded=false</code>.

### 2. Monitor snapshot consumption

The run reused the existing zero-model-token monitor snapshot. Monitor history is useful for
continuity but cannot satisfy a formal Top target. During this regeneration, the loader and
collector excluded rows carrying <code>retained_from_previous_snapshot</code>. The final r7
index contains zero retained-history root rows.

If an eligible monitor cache had fewer current items than the source target, collection
performed a live fetch. A code-review regression found that the earlier mixed path placed
monitor rows before live rows. The corrected rule makes successful live acquisition the
ranking authority and appends only deduplicated monitor tail fallback. If live acquisition
produces no candidates, eligible monitor rows remain the fallback rather than being discarded.

### 3. Source collection, normalization, and ordering

[collect_source](../../src/daily_intelligence/collector.py) and the specialized adapters read
untrusted RSS, Atom, public HTML, rankings, and approved source pages. Each source result then
passed through:

1. URL/title normalization;
2. canonical-URL deduplication;
3. original input-position assignment to <code>metadata.source_rank</code>;
4. selected <code>item_order</code>;
5. per-source target/cap construction;
6. root and compatibility-mirror synchronization;
7. immutable index persistence.

<code>source</code> order sorts current rows by original <code>source_rank</code> and places
retained history after current rows. <code>published_at</code> sorts parseable publication times
descending while preserving stable input order for missing or tied timestamps. Neither mode
rewrites <code>source_rank</code>.

### 4. Exact source propagation

The table is recomputed from final index r7 and report r3. “Collected” is the number of current
items in the final source group. “Reported” is the number of ordinary briefs. A successful
source with fewer than 15 real candidates reports the actual count; no fabricated filler is
used.

| Source | Final status | Collected | Reported | Preserved acquisition detail |
| --- | --- | ---: | ---: | --- |
| <code>weibo_hot</code> | success | 50 | 15 | |
| <code>yicai_economy</code> | success | 40 | 15 | |
| <code>nbs_china_releases</code> | success | 15 | 15 | HTTP disconnect retained as diagnostic after usable rows |
| <code>pboc_monetary_reports</code> | success | 1 | 1 | |
| <code>sec_edgar_latest</code> | verification_required | 0 | 0 | HTTP 403 |
| <code>federal_reserve_releases</code> | success | 20 | 15 | |
| <code>hacker_news</code> | success | 20 | 15 | |
| <code>lobsters</code> | success | 25 | 15 | |
| <code>infoq_ai</code> | success | 22 | 15 | live rows are authoritative; cache is tail fallback |
| <code>anthropic_research</code> | success | 11 | 11 | feed discovery failed, public-page collection supplied rows |
| <code>bytedance_seed_papers</code> | success | 20 | 15 | |
| <code>openai_publications</code> | success | 40 | 15 | |
| <code>deepmind_publications</code> | success | 30 | 15 | feed discovery failed, public-page collection supplied rows |
| <code>huggingface_papers</code> | success | 13 | 13 | Trending Top is authoritative even when papers are older |
| <code>papers_with_code</code> | success | 13 | 13 | |
| <code>google_research_publications</code> | success | 16 | 15 | feed discovery failed, public-page collection supplied rows |
| <code>microsoft_research_publications</code> | success | 10 | 10 | |
| <code>nvidia_research_publications</code> | success | 32 | 15 | feed discovery failed, public-page collection supplied rows |
| <code>arxiv_ai</code> | success | 40 | 15 | |
| <code>github_trending</code> | success | 18 | 15 | |
| <code>cnbc_world</code> | success | 30 | 15 | |
| <code>reuters</code> | verification_required | 0 | 0 | HTTP 401 |
| <code>abc_news</code> | success | 15 | 15 | feed discovery failed, public-page collection supplied rows |
| <code>guardian_uk</code> | success | 40 | 15 | |
| <code>bbc_world</code> | success | 29 | 15 | |
| <code>forbes</code> | success | 50 | 15 | feed discovery failed, public-page collection supplied rows |
| <code>twz</code> | success | 33 | 15 | |
| <code>defence_blog_aviation</code> | partial | 10 | 10 | ten real candidates followed by HTTP 403 |
| <code>the_aviationist</code> | success | 15 | 15 | |
| <code>usni_news</code> | success | 30 | 15 | |
| <code>rusi_publications</code> | success | 21 | 15 | |
| <code>yahoo_news</code> | rate_limited | 0 | 0 | HTTP 429 |

Totals: 32 configured sources, 28 success, two verification-required, one partial, one
rate-limited, 709 candidates, and 29 represented report sources.

### 5. Index r6 and context r6

Collection completed at 10:59:18 and wrote immutable index r6. [build_context](../../src/daily_intelligence/context.py)
then built a bounded downstream contract rather than passing all raw source payloads to a model.
Context compaction preserved current index order and retained at most the configured compact
candidate envelope per source.

The context contained:

- 565 compact candidates;
- 29 source plans;
- 403 ordered <code>default_item_ids</code>;
- a semantic-cache decision for every planned item;
- one bounded authoring batch for the remaining gap after reuse.

### 6. Selection and full-text enrichment

Twelve selected IDs entered [enrich_edition](../../src/daily_intelligence/workflow.py) at
11:00:20:

<code>twz-4582ce2507ce</code>, <code>cnbc_world-75fa3f096460</code>,
<code>bbc_world-7bef5564d15e</code>, <code>guardian_uk-9f92cc1a9884</code>,
<code>cnbc_world-53f2c23e264d</code>, <code>cnbc_world-aafa912ecfbf</code>,
<code>yicai_economy-acf17f094d66</code>, <code>nbs_china_releases-f62797899b9f</code>,
<code>openai_publications-c7160f6694ef</code>, <code>twz-b113f21e9de1</code>,
<code>arxiv_ai-60605cbf67b7</code>, and <code>arxiv_ai-75507c9762ca</code>.

All 12 were attempted by HTTP with global concurrency three and per-domain concurrency one.
Six produced full text: two TWZ items, one BBC item, one NBS item, and two arXiv items. Six
preserved verification/no-body outcomes: three CNBC, Guardian, Yicai, and OpenAI. No browser
fallback was invoked. Total measured enrichment time was 2.642 seconds.

Enrichment created immutable index r7 with <code>derived_from</code> r6 and rebuilt context r7.
It did not mutate either predecessor.

### 7. Rank-plan boundary

For every one of the 29 represented sources, the report item-ID sequence was recomputed and
compared to context r7 <code>brief_plan.default_item_ids</code>. All 29 matched exactly.

The final TWZ item sequence is:

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

The post-evaluation HTML was parsed independently. It contains 29 source groups. TWZ and InfoQ
each render the exact sequence <code>来源Top1</code> … <code>来源Top15</code>, and Weibo renders
<code>热搜Top1</code> … <code>热搜Top15</code>.

### 8. Semantic reuse and authoring

[begin_authoring_session](../../src/daily_intelligence/authoring.py) bound the session to
attempt 4, context r7, its hash, the 403-item plan, and the runtime deadline. Semantic-cache
reuse was allowed only where item ID, semantic fingerprint, output language, prior evaluation,
and current plan membership all matched.

- 386 briefs were reused.
- 17 briefs required new writing.
- One packet contained exactly those 17 <code>author_item_ids</code>.
- The packet was accepted at 11:03:44.
- Receipt wall duration was 189 seconds.
- Deterministic assembly merged 403 briefs with no missing or recovered batches.
- Merge time was 0.025 seconds.

Batch result order never controls report order. The assembler always re-reads each source plan
and reconstructs the report by its ordered <code>default_item_ids</code>.

### 9. Media flow

Asynchronous media prefetch inspected all 403 planned items:

- 146 image candidates;
- 121 attached at prefetch time;
- 105 unique files;
- 104 cache reuse hits;
- 25 failures;
- zero budget skips;
- 23,638,452 bytes;
- 1.694 seconds.

The final save boundary retried/materialized report media and recorded 139 attached images,
123 unique files, 104 reused files, seven safe failures, and 26,000,222 bytes. The seven final
warnings were non-public-address image omissions; no article was removed.

### 10. Analysis authoring and repair

The bounded analysis packet contained 18 candidates. The authored draft selected eight events
across geopolitics, AI/technology, and markets. All structured evidence IDs were checked against
the candidate set.

Two repair rounds occurred before acceptance:

1. validation rejected one featured event with two source items and a stakeholder name lacking
   Chinese text; the event retained its TWZ evidence and the stakeholder became
   <code>Anthropic公司</code>;
2. validation found analysis/synthesis evidence references to a removed Aviationist item; the
   two structured references were removed.

The final pre-save validation returned zero errors and zero warnings. Analysis telemetry was
1,760 seconds; total authoring telemetry was 1,990 seconds and includes human/agent wait time,
not just CPU work.

### 11. Compile, validation, and immutable persistence

[compile_report_data](../../src/daily_intelligence/reporting.py) restored deterministic fields,
bound briefs to the authoritative index and plans, generated rank labels, normalized the
seven-section shell, and validated schema/evidence/coverage semantics. Draft-only validation
used an in-memory report ID and revision so it no longer fails merely because an unsaved draft
has no persistent identity.

The report saved at 11:35:20 with:

- report ID <code>daily-2026-08-05-morning-r3</code>;
- content hash
  <code>e5277bfad7c9af2202c0c507d14e4354f5f56781a14683e044c703371e77f119</code>;
- 403 briefs;
- 29 represented sources;
- eight featured events;
- status distribution 119 NEW, 16 UPD, 268 WATCH;
- access distribution 391 metadata-only, six full-text, six verification-required.

Section totals reconcile exactly:

| Section | Briefs |
| --- | ---: |
| International | 45 |
| Domestic news | 15 |
| Military | 70 |
| Markets | 76 |
| Technology news | 45 |
| Papers worth reading | 137 |
| Open-source projects | 15 |
| **Total** | **403** |

Save timings were 0.153 seconds compile/validation, 65.566 seconds media, 0.076 seconds immutable
persistence, 1.683 seconds initial local output, 0.178 seconds state update, and 67.942 seconds
total.

### 12. Local projections and deferred tail

The main path wrote JSON, Markdown, HTML, desktop HTML, and the archive index before returning.
The deferred tail ran from 11:35:39 to 11:38:46. Edge PDF projection consumed 181.606 seconds;
the PDF-ready milestone was 11:38:41. The tail completed with no warning or error.

The independent evaluator later refreshed HTML/PDF. Post-evaluation rank parsing was repeated
after that refresh so a stale renderer could not silently restore importance ordering.

### 13. Independent evaluation

The corrected independent evaluation was persisted at 14:25:59 as
<code>evaluation-daily-2026-08-05-morning-r3-r3</code>. It scored 36/45 and assigned 5/5 to
<code>importance_ordering</code> after confirming that all ordinary source groups preserve the
canonical Top sequence. Its continuity decision is <code>selective</code>, excluding
<code>analyses</code> and <code>event_summaries</code> from reuse until the identified evidence
and TL;DR defects are repaired. The report itself remains accepted and readable; the excluded
categories prevent known defects from re-entering future semantic continuity.

The first scheduled attempt exposed two version-boundary failures rather than a report-order
failure:

1. a Windows host does not guarantee a PowerShell execution shell; Hermes ran the prompt through
   Git Bash, so a PowerShell-specific <code>$env:PYTHONPATH</code> prefix did not bind the CLI
   to canonical source;
2. the evaluator read an old installed skill contract and incorrectly demanded that ordinary
   briefs be re-sorted by importance.

The scheduler now injects canonical <code>src/</code> into Python itself using a shell-neutral
<code>python -c</code> launcher, provides the absolute canonical contract path, and states the
ordinary-brief ordering rule explicitly. Focused tests assert that neither PowerShell nor POSIX
<code>PYTHONPATH</code> syntax appears in the scheduled prompt.

## Timeline

| Stage | Timestamp | Observed transition |
| --- | --- | --- |
| created | 10:58:16 | Attempt-4 manifest and deadline persisted |
| collecting | 10:58:18 | Source acquisition began |
| building_context | 10:59:18 | Immutable collection index existed |
| awaiting_selection | 10:59:18 | Context r6 exposed selection candidates |
| extracting_content | 11:00:20 | Twelve selected IDs entered enrichment |
| awaiting_authoring | 11:00:23 | Immutable r7 index/context existed |
| brief receipt | 11:03:44 | One 17-item batch accepted |
| finalizing | 11:34:14 | Final report assembly began |
| report persisted | 11:35:20 | JSON/Markdown r3 became authoritative |
| completed_partial | 11:35:22 | Local report complete; four source states pending |
| tail started | 11:35:39 | Deferred PDF work began |
| PDF ready | 11:38:41 | Initial Edge PDF existed |
| tail completed | 11:38:46 | Local tail completed without errors |
| evaluation refresh | 14:28:47 | Corrected independent evaluation and final Edge PDF completed |

## Code Review: Defects Fixed During This Run

| Severity | Defect | Effect | Correction and evidence |
| --- | --- | --- | --- |
| High | Retained monitor-history rows could satisfy a formal source target. | Old rows could occupy Top positions and suppress live fallback. | Filtered on monitor load and defensively in collection; added a 10-current + 10-retained + target-15 regression. |
| High | Successful mixed live/cache collection placed cache rows before live rows. | A newly promoted live Top1 could appear after old monitor rows; InfoQ was affected in the intermediate attempt. | Successful live results now own the ranking prefix; deduplicated monitor rows are tail fallback only. Failure/no-result still keeps cache fallback. |
| High | The evaluator used a stale installed CLI and stale contract. | Evaluation could refresh correct HTML/PDF using old importance ordering and report a false ordering defect. | Added a shell-neutral canonical-source launcher, absolute contract path, explicit plan-order rubric, and focused scheduler test. |
| Medium | Resume-index merge appended retried sources at the end. | Source-group order changed after a retry. | Retried sources now replace their original positions; regression verifies A/B/C remains A/B/C. |
| Medium | Draft validation expected a persisted report ID/revision. | A valid unsaved authoring draft could fail identity checks. | Validation compiles a deep copy with validation-only identity and leaves the input unchanged. |
| Low | A context docstring implied enriched items were promoted. | Documentation contradicted the actual canonical-order invariant. | Corrected the maintained semantic contract. |

Focused monitor and architecture coverage passed after each correction. The final full gate is
recorded below.

## Code Review: Open Gaps and Optimizations

These were observed but not silently widened into unrelated refactors:

- enrichment is not yet checkpointed/re-entrant after a process-wide failure;
- authoring packets are overwrite-capable and are not individually hash-bound to context;
- acquisition health and cache availability are not fully separated;
- context compaction can omit an explicitly enriched item below its compact prefix;
- JSON and Markdown do not share one revision transaction;
- evaluation derivative refresh is not fully idempotent across retries;
- batch timing starts too early and rejected repair attempts are not durably retained;
- live transport metrics combine browser and HTTP;
- monitor snapshot eligibility and projection-ready milestones use separate checks;
- <code>published_at</code> sorts only the adapter-returned bounded candidate set, not an
  unlimited source corpus;
- standalone <code>save-report</code> can be called without the run-owned brief-plan boundary;
- verified multi-page merge semantics need stronger characterization tests;
- the 45-item authoring batch target is soft, not a hard cap;
- the final PDF is approximately 99 MB and needs image downsampling/embedding optimization;
- reused semantic cache propagated nine TL;DR strings containing internal packet/data wording;
- analytical continuity text should compare to the immediately preceding revision, and every
  inline evidence ID should be machine-checked against featured-event evidence.

The durable subset is mirrored in the
[technical-debt tracker](tech-debt-tracker.md).

## Cleanup and Version Decisions

The repository has one local branch (<code>main</code>), one worktree, and no stash or detached
working copy. The apparent “many code versions” were generated/install copies rather than Git
branches.

During closeout, these ignored, recoverable targets were sent to the Windows Recycle Bin:

- <code>E:/ai_project/daily-intelligence-skill/build</code>;
- <code>E:/ai_project/daily-intelligence-skill/dist</code>;
- <code>E:/ai_project/daily-intelligence-skill/output/hermes-tap-smoke</code>;
- <code>E:/ai_project/daily-intelligence-skill/output/signaltrail-tap-premerge-backup</code>.

The canonical <code>src/</code>, tests, documentation, user-authored dirty changes, tracked
<code>skills/signaltrail/</code> release snapshot, and final r3 chain were preserved. The
tracked release snapshot is intentionally not edited by hand.

The invalid user-facing report r1 and intermediate r2 files were moved, not destroyed, to
<code>C:/Users/wmf/AppData/Local/hermes/daily-intelligence/retrospectives/2026-08-05/invalid-revisions/reports</code>.
The two stale-contract evaluation artifacts and their shared draft were moved under the peer
<code>evaluations</code> archive. The regenerated local archive index has exactly one card for
2026-08-05: morning r3.

The supported installer then synchronized
<code>C:/Users/wmf/AppData/Local/hermes/skills/research/signaltrail</code>. All 31 installed
Python modules matched canonical source hashes. Three legacy <code>daily-intelligence</code>
skill directories were sent to the Windows Recycle Bin; <code>hermes skills list</code> now
shows only enabled <code>signaltrail</code> for this workflow.

## Verification

Recomputed artifact checks:

- r7 root items: 709;
- r7 nested mirror items: 709;
- retained-history root items: zero;
- context candidates/plans/planned: 565 / 29 / 403;
- semantic reuse/new authoring/batches: 386 / 17 / 1;
- report briefs/sources/events: 403 / 29 / 8;
- all 29 report source sequences equal their plan sequences;
- TWZ, Weibo, and InfoQ post-evaluation HTML labels are exact continuous Top1–15;
- schema/report validation: zero errors, zero warnings;
- run deadline exceeded: false.

Final file checksums:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Report JSON r3 | 735,242 | <code>f9ce3a794fbaf8012ea09774264aff062184ddcbf7b598265d12408da3ed5e87</code> |
| Report Markdown r3 | 252,164 | <code>5ee7f53506141e3b3a47d01eb7f7bc809585174d6f592d0237c3e9bea6770210</code> |
| Report HTML r3 | 542,936 | <code>cd7c658730562454a0172d99eb86acec53fff1cf4e2312bc7eee971936258f9b</code> |
| Report PDF r3 (155 pages) | 98,974,490 | <code>8e6e10d286d241d59338d5bbda902e176d0c00eefffd7d6fe99a9e19710a637a</code> |
| Independent evaluation r3 | 5,500 | <code>43ce71a98b333d5d0ac1515ad847c812681790bfa6d000df7a828add97e376b6</code> |
| Final index r7 | 2,547,312 | <code>846f5b764ea859de1e22a5ee29a7ba86e1ab9180ee3244a5b0ff4b91e42a7bd9</code> |
| Final context r7 | 1,215,194 | <code>00273c941f49c7260da29584a14b946050d2e54ec1eec4db4c92258542a0980e</code> |

Repository gate:

~~~powershell
python -m pytest
python -m ruff check .
python -m compileall -q src tests scripts
python scripts/check_code_comments.py
python scripts/check_docs.py
git diff --check
~~~

Final results: 236 tests passed in 24.37 seconds; Ruff passed; compileall passed; all 34
maintained Python files passed the Chinese semantic-comment gate; documentation checks passed
for 10 canonical records and 49 Markdown files; <code>git diff --check</code> passed. The
installed CLI separately returned report validation <code>{"errors": 0, "warnings": 0}</code>.

## Recovery

- Runtime/code copies removed during cleanup are recoverable from the Windows Recycle Bin.
- Report r3, index r6/r7, context r7, authoring receipts, run manifest, and corrected evaluation
  are the retained final chain.
- Intermediate evidence needed to explain semantic reuse remains in runtime lineage; user-facing
  invalid projections are not kept in the archive.
- If a projection must be regenerated, use report r3 JSON as the fact source and canonical root
  source code. Do not regenerate semantic content merely to refresh HTML or PDF.
