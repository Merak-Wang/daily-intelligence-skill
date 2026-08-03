# Engineering Records

**Status:** Verified
**Owner:** Repository maintainers
**Last verified:** 2026-08-02

This directory is SignalTrail's engineering system of record. `AGENTS.md` points here;
it does not duplicate the content. English records are canonical, with matching Chinese
translations under [`zh-CN/`](zh-CN/README.md).

## Catalog

| Record | Question answered | Status |
| --- | --- | --- |
| [Architecture](../ARCHITECTURE.md) | What are the system boundaries and dependency directions? | Verified |
| [Core beliefs](design-docs/core-beliefs.md) | Which principles decide trade-offs? | Verified |
| [Design catalog](design-docs/index.md) | Which design records are authoritative or detailed? | Verified |
| [Execution plans](exec-plans/index.md) | What work is active, completed, or tracked as debt? | Verified |
| [Technical-debt tracker](exec-plans/tech-debt-tracker.md) | Which known gaps still need a decision or refactor? | Verified |
| [Product-spec catalog](product-specs/index.md) | Where are user and report contracts defined? | Verified |
| [Quality score](quality-score.md) | How complete and trustworthy is repository knowledge? | Verified |
| [Operations runbook](../references/runbook.md) | How are runs operated and recovered? | Verified detail |
| [Editorial policy](../references/editorial-policy.md) | What evidence and selection rules apply? | Verified detail |
| [System design detail](../references/system-design.md) | What are the detailed data and state contracts? | Verified detail |

## Record Status

- **Draft:** under active review; not yet a decision source.
- **Verified:** checked against current code and tests on the stated date.
- **Historical:** retained for rationale, not current behavior.
- **Generated:** produced mechanically; regenerate instead of editing.

If a document and implementation disagree, follow the precedence in
[`AGENTS.md`](../AGENTS.md) and fix the document in the same change.

## How to Change Documentation

1. Update the smallest authoritative English record.
2. Update its matching file under `docs/zh-CN/`.
3. Update indexes, status, owner, and verification date when scope or authority changes.
4. Link to details instead of copying them into `AGENTS.md` or another overview.
5. Run `python scripts/check_docs.py` and `python -m pytest tests/test_docs.py`.

New design decisions belong in `design-docs/`. Multi-step implementation work belongs in
`exec-plans/`; move completed plans instead of deleting their rationale. User-facing behavior
belongs in README/product contracts, and operating detail belongs in `references/`.

Maintained Python functions and classes carry concise semantic Chinese logic/input/output notes.
Inputs identify their upstream source and consumed information; outputs state what the result
means to the next stage. Type-only or function-name paraphrases fail
`python scripts/check_code_comments.py`. Keep inline comments focused on non-obvious decisions.

## Language Policy

English is the canonical engineering record because it is the repository's shared agent and
contributor interface. Chinese translations are reviewable mirrors for maintainers and must be
updated in the same change. A translation never silently introduces a rule absent from English.
