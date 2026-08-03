# Examples

**Status:** Historical samples and synthetic test fixtures
**Last verified:** 2026-08-02

[简体中文](README.md) | [English](README.en.md)

This directory contains two complementary product views.

## Report gallery

The archived HTML editions demonstrate the report experience at operational scale:

| Edition | Collection scale | Editorial result |
| --- | ---: | ---: |
| [2026-07-24 morning r3](reports/2026-07-24-morning-r3.html) | 24 sources · 197 updates | 10 priority events |
| [2026-07-25 morning r1](reports/2026-07-25-morning-r1.html) | 29 sources · 235 updates | 10 priority events |

They are preserved schema 1.5 reports and retain the earlier **Daily Intelligence**
masthead. Current schema 2.0 editions add cross-perspective synthesis and use the
**SignalTrail** brand. Download the HTML files and open them locally for the complete
interactive reading experience.

## Synthetic fixtures

`sample_input.json` and `sample_report.json` support automated tests, schema
validation, and output rendering.

- People, organizations, events, dates, and analysis are synthetic.
- `news.example` and `wire.example` are reserved example domains, not publishers.
- Fixtures are engineering inputs, not factual sources or editorial templates.
- After changing `sample_report.json`, run the report, architecture, and skill tests.
