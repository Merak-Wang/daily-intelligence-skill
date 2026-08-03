# Product Specification Catalog

**Status:** Verified
**Owner:** Repository maintainers
**Last verified:** 2026-08-02

SignalTrail currently keeps product contracts close to their audience rather than duplicating
them in this directory:

| Contract | Audience | Authority |
| --- | --- | --- |
| [README](../../README.en.md) | Users and evaluators | Product scope, install, output, examples |
| [Skill procedure](../../SKILL.md) | Hermes agents | Runtime workflow and verification checklist |
| [Report contract](../../templates/report-contract.md) | Authoring agents and validators | Draft structure and semantic constraints |
| [Report schema](../../schemas/report.schema.json) | Code and tooling | Machine-enforced report shape |
| [Editorial policy](../../references/editorial-policy.md) | Authors and reviewers | Evidence, selection, and source rules |

Create a dedicated product spec only when a proposed capability is not yet represented by a
stable user contract. Mark it Draft until implementation, tests, and user documentation land.
