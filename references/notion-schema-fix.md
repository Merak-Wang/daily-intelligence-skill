# Notion Schema Compatibility Migration

This file is retained for backward-compatible links. Do not rename, delete, or recreate properties
in a shared Notion data source merely to satisfy the publisher.

Daily Intelligence v0.4 reads the live schema and automatically selects either the `hermes_notes`
or `daily_intelligence` profile from `configs/notion.yaml`. If neither profile matches, use the
actionable error to correct the local mapping or deliberately create a separate data source.

See `references/notion-setup.md` for supported schemas, data-source ID diagnosis, credentials, and
safe migration guidance.
