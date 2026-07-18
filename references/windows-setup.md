# Native Windows Setup

## Hermes Home

Native Windows Hermes stores its active profile under `%LOCALAPPDATA%\hermes`, not
`%USERPROFILE%\.hermes`. The default locations are:

```text
%LOCALAPPDATA%\hermes\skills
%LOCALAPPDATA%\hermes\daily-intelligence
%LOCALAPPDATA%\hermes\browser-profiles\daily-intelligence
%LOCALAPPDATA%\hermes\.env
```

`HERMES_HOME` overrides this root. Unix and WSL use `~/.hermes` when no override is set.

## Installation and moving the directory

Put the Skill in its final directory before installing the Python package. Use an editable install
for development only:

```powershell
python -m pip install -e .
```

For a stable installed copy, use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

`ExecutionPolicy Bypass` applies only to that child process; it does not change the machine or user policy. The installer mirrors the repository into the real Hermes skills directory while excluding secrets, runtime data, browser profiles, authenticated HTML, screenshots and Playwright debug output.

An editable install records the source directory. Uninstall it before moving or renaming that
directory, then reinstall from the new location:

```powershell
python -m pip uninstall daily-intelligence-skill
```

## Environment

The CLI loads missing values from `%LOCALAPPDATA%\hermes\.env` automatically and does not override
variables already supplied by the process. Do not use `export $(grep ... | xargs)`: it mishandles
quoted values and can expose secrets.

## Canonical data root

The first normal command binds one runtime root in `%LOCALAPPDATA%\hermes\state\daily-intelligence-data-root.json`. A later command using another root fails before it reads or writes run artifacts. Inspect or deliberately migrate the binding with:

```powershell
daily-intel --data-dir "$env:LOCALAPPDATA\hermes\daily-intelligence" data-root status
daily-intel --data-dir "$env:LOCALAPPDATA\hermes\daily-intelligence" data-root adopt
```

Adoption does not merge or delete an old directory.

## Local HTML and PDF

Every successful `finalize-edition` creates a local reading page, A4 PDF, and archive index under:

```text
%LOCALAPPDATA%\hermes\daily-intelligence\reports\index.html
```

This does not require Notion credentials. Windows uses installed Edge to print the same HTML to PDF without the authenticated browser profile or external network requests. If headless Edge is unavailable, the installed ReportLab dependency produces a simpler Chinese PDF instead. Set `output.pdf_engine: reportlab` in `configs/sources.yaml` to force that fallback, or remove `pdf` from `output.formats` to generate HTML only.

`open_after_finalize` defaults to false so 06:00/18:00 tasks do not open a window. Set it true only for interactive use. HTML/PDF may be refreshed after independent evaluation; JSON/Markdown remain the immutable facts.

## Manual source verification

Interactive `run-edition` opens the verification queue by default only when pending pages exist. In a desktop PowerShell or interactive Hermes session, run:

```powershell
daily-intel run-edition --edition morning --profile-dir "$env:LOCALAPPDATA\hermes\browser-profiles\daily-intelligence" --verification-timeout-seconds 180
daily-intel verify-source reuters --browser-channel msedge
daily-intel verify-pending --index "C:\path\to\index.json" --browser-channel msedge --timeout-seconds 300
```

After collection the connected queue opens only when failed, challenged, or rate-limited pages exist. Every scheduled or unattended run must pass `--unattended`. `verify-pending` remains the manual reopen path, while `--open-verification` remains a compatibility alias.

Windows defaults to installed Microsoft Edge when no channel override is present. Complete the
legitimate publisher login or verification in the visible dedicated Edge profile. Cookies and login
state persist in that profile for later runs. The CLI does not require terminal input: it detects a
cleared challenge, immediately extracts the current authenticated page, and atomically adopts a new
index. A closed tab, HTTP 403, extraction failure, or timeout is skipped and remains pending with
its original link. Do not run `resume` after `verify-pending`; use it only for a later diagnostic
retry. Scheduled cron/gateway jobs must pass `--unattended`, keep challenges pending, and publish a partial report instead
of waiting for a GUI.
