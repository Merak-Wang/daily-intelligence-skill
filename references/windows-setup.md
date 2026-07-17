# Native Windows Setup

## Hermes Home

Native Windows Hermes stores its active profile under `%LOCALAPPDATA%\hermes`, not
`%USERPROFILE%\.hermes`. The default locations are:

```text
%LOCALAPPDATA%\hermes\skills
%LOCALAPPDATA%\hermes\data\daily-intelligence
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
python -m pip install .
```

An editable install records the source directory. Uninstall it before moving or renaming that
directory, then reinstall from the new location:

```powershell
python -m pip uninstall daily-intelligence-skill
```

## Environment

The CLI loads missing values from `%LOCALAPPDATA%\hermes\.env` automatically and does not override
variables already supplied by the process. Do not use `export $(grep ... | xargs)`: it mishandles
quoted values and can expose secrets.

## Manual source verification

Challenge detection is headless by default and only records `verification_required`. It does not
open a browser window. In a desktop PowerShell or interactive Hermes session, run:

```powershell
daily-intel run-edition --edition morning --profile-dir "$env:LOCALAPPDATA\hermes\browser-profiles\daily-intelligence" --open-verification --verification-timeout-seconds 180
daily-intel verify-source reuters --browser-channel msedge
daily-intel verify-pending --index "C:\path\to\index.json" --browser-channel msedge --timeout-seconds 300
```

The `run-edition` flag is the preferred interactive path: after collection it opens the connected
queue only when failed, challenged, or rate-limited pages exist. Omit it from every scheduled or
unattended run. `verify-pending` remains the manual reopen path.

Windows defaults to installed Microsoft Edge when no channel override is present. Complete the
legitimate publisher login or verification in the visible dedicated Edge profile. Cookies and login
state persist in that profile for later runs. The CLI does not require terminal input: it detects a
cleared challenge, immediately extracts the current authenticated page, and atomically adopts a new
index. A closed tab, HTTP 403, extraction failure, or timeout is skipped and remains pending with
its original link. Do not run `resume` after `verify-pending`; use it only for a later diagnostic
retry. Scheduled cron/gateway jobs must omit `--open-verification`, keep challenges pending, and publish a partial report instead
of waiting for a GUI.
