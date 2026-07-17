# Notion Setup

## Existing Hermes Notes data source

The `hermes_notes` profile targets the existing `Hermes Notes` schema:

| Property | Type | Daily Intelligence value |
|---|---|---|
| Name | Title | Report title |
| Date | Date | Report date |
| Status | Status | `New` for morning, `Reviewed` for evening |
| Source | Select | `Daily Intelligence` |
| Tags | Multi-select | `Daily Intelligence` plus edition |

Do not mutate this shared data source automatically. The publisher validates every configured
property name, type, and configured status option before creating or updating a page. A mismatch is
reported as an actionable error instead of being silently skipped.

## Optional dedicated data source

The `daily_intelligence` profile targets a separate data source with these additional properties:

| Property | Type |
|---|---|
| Version | Select |
| Source Count | Number |
| Event Count | Number |
| Pending Verification | Number |

The publisher reads the actual schema and selects the first fully compatible profile. If neither
profile matches, it reports every missing or incompatible property instead of guessing or mutating
the data source. Legacy configs with top-level `properties` and `values` remain supported.

The local JSON and Markdown reports remain the source of truth, so omitting optional Notion
properties does not discard report data.

## Credentials

Set:

```powershell
$env:NOTION_TOKEN = "ntn_..."
$env:NOTION_DATA_SOURCE_ID = "..."
```

Grant the connection read, insert, and update content capabilities and explicitly connect it to the target database/data source.

For Notion URLs shaped like `/ds/{workspace_uuid}/{data_source_uuid}`, set
`NOTION_DATA_SOURCE_ID` to the second UUID. The first UUID identifies the workspace/container and
returns 404 when passed to `/v1/data_sources/{id}`. If the URL is ambiguous, query Notion search or
`GET /v1/data_sources/{candidate}` and accept only the candidate that returns the expected property
schema. Never print the token while diagnosing the ID.

Do not put the token in `SKILL.md`, source code, YAML committed to Git, or model-visible output.

## Publishing behavior

- Morning creates today's page if none exists and appends a `06:00 Morning Edition` section.
- Evening finds the same page by `Date`, appends an `18:00 Evening Edition` section, and updates page properties.
- The local registry prevents duplicate edition blocks during retries.
- `--republish` is available for deliberate republishing and never bypasses validation.

## Page contents

Publish:

- executive-summary callout and table of contents,
- the three colored top-level groups 资讯、技术、研判,
- all seven fixed content subsection titles, including empty sections,
- linked source headings and numbered importance-sorted events,
- TL;DR, importance callouts, collapsible evidence and optional public images,
- evidence-backed judgement and next-day watch signals,
- source links,
- pending verification summary,
- a nine-dimension quality table and an editable user-feedback callout.

The publisher reads the edited feedback marker from the latest registered Notion page during the
next prepare step and stores structured feedback locally. A missing page, marker, or remote read
failure never blocks local report generation.

Do not publish:

- saved article bodies,
- raw HTML,
- cookies or profile data,
- hidden or paywalled text,
- internal model instructions.
