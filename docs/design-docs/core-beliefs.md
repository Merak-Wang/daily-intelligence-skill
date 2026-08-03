# Core Beliefs

**Status:** Verified
**Owner:** Repository maintainers
**Last verified:** 2026-08-02

1. **The repository is the record.** Local code, versioned artifacts, tests, and linked
   documents must be sufficient to reconstruct why the system behaves as it does.
2. **Give agents a map, not a manual.** Small entry documents preserve context for the task,
   code, and evidence that actually matter.
3. **Determinism surrounds model judgment.** Models may select and write; Python must own
   identity, state, validation, limits, and persistence.
4. **A failure is data.** Access denial, rate limiting, and partial collection remain explicit;
   hiding them as empty success corrupts downstream decisions.
5. **Local truth precedes remote convenience.** JSON/Markdown are authoritative. HTML, PDF,
   desktop copies, and Notion are retryable projections.
6. **Context and cost are budgets.** Collection may be broad, but evidence packets, authoring
   batches, and analysis dossiers stay bounded.
7. **Compatibility is an explicit boundary.** Legacy source-index shapes remain readable until
   a deliberate migration removes them with tests and release notes.
8. **Documentation must be testable.** Catalog coverage, translation pairs, link integrity, and
   entry-file size are mechanical checks, not reviewer memory.
9. **Generated copies are downstream.** Implement once in canonical sources and rebuild release
   artifacts; never maintain several drifting implementations by hand.
10. **Known debt stays visible.** Record a scoped ownerless next action and evidence instead of
    burying gaps in a broad instruction file.
11. **Comments explain contracts and choices.** Maintained definitions state their logic, input
    provenance and consumed fields, and downstream output meaning in concise Chinese. Types and
    identifier paraphrases are insufficient; inline notes are reserved for non-obvious safety,
    state, compatibility, concurrency, and recovery decisions.
