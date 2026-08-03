# Repository Knowledge Quality Score

**Status:** Verified
**Owner:** Repository maintainers
**Last verified:** 2026-08-02
**Method:** Code/test/document audit; 1 (missing) to 5 (strong)

| Area | Score | Evidence | Gap |
| --- | ---: | --- | --- |
| Entry map and routing | 5 | Concise `AGENTS.md`, task router, source precedence | Keep under the enforced line limit |
| Architecture | 4 | Layer, flow, state, and artifact maps tied to modules | Detailed Chinese reference still overlaps the summary |
| Product/runtime contracts | 5 | Schema, report contract, `SKILL.md`, README, tests | Keep version statements synchronized |
| Reliability and persistence | 5 | Atomic writers, immutable revisions, explicit statuses, recovery tests | Cross-process mutable-state locking remains caller-owned |
| Test determinism | 5 | Monitor clock injection; 215 tests isolated from production data | Continue rejecting wall-clock fixtures |
| Code readability | 4 | Typed helpers; 439 semantic Chinese definition contracts; critical inline rationale; AST check rejects type-only wording | Several validation/render functions remain very large |
| Documentation integrity | 4 | Catalog, translations, links, size checks | Existing detailed references are not yet bilingual |
| Release-source hygiene | 2 | Allowlisted builder exists | Checked-in `skills/signaltrail/` snapshot is duplicated and drifted |

**Total: 34/40.** The repository has strong behavioral contracts and local-data safety. The
largest remaining risks are duplicated release sources and oversized validation/rendering
functions. Track follow-up in [the technical-debt tracker](exec-plans/tech-debt-tracker.md).

Re-score when a boundary, schema, status model, documentation authority, or packaging model
changes. A score change must cite code/tests or a cataloged record; it is not a sentiment poll.
