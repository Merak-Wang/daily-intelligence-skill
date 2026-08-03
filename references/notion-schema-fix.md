# Notion Schema Compatibility Migration

**Status:** Historical compatibility note
**Last code verification:** 2026-08-02
**Current setup:** [`references/notion-setup.md`](notion-setup.md)

This file is retained for backward-compatible links. Do not rename, delete, or recreate properties
in a shared Notion data source merely to satisfy the publisher.

SignalTrail (formerly Merak Brief / Daily Intelligence) reads the live schema and automatically selects either the `hermes_notes`
or `daily_intelligence` profile from `configs/notion.yaml`. If neither profile matches, use the
actionable error to correct the local mapping or deliberately create a separate data source.

See `references/notion-setup.md` for supported schemas, data-source ID diagnosis, credentials, and
safe migration guidance.
