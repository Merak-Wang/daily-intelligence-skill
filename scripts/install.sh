#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hermes_home="${HERMES_HOME:-${HOME}/.hermes}"
skills_root="${hermes_home}/skills"
target_dir="${skills_root}/research/daily-intelligence"
editable=false
dev=false
for arg in "$@"; do
  case "${arg}" in
    --editable) editable=true ;;
    --dev) dev=true ;;
    *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

python - "${skill_dir}" "${skills_root}" "${target_dir}" <<'PY'
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1]).resolve()
skills_root = Path(sys.argv[2]).expanduser().resolve()
target = Path(sys.argv[3]).expanduser().resolve()
try:
    target.relative_to(skills_root)
except ValueError as exc:
    raise SystemExit(f"Refusing to synchronize outside the Hermes skills directory: {target}") from exc

ignored = {
    ".agents", ".git", ".github", ".playwright-cli", ".pytest_cache", ".ruff_cache",
    "__pycache__", "build", "dist", "data", "daily-intelligence", "daily-intel-data",
    "browser-profile", "browser-profiles", "edge-profile", "raw_html", "screenshots",
    "daily_intelligence_skill.egg-info",
}

def ignore(_directory: str, names: list[str]) -> set[str]:
    blocked = {name for name in names if name in ignored}
    blocked.update(name for name in names if name == ".env" or name.endswith(".cookies.json"))
    blocked.update(name for name in names if name.endswith((".har", ".storage-state.json")))
    return blocked

target.parent.mkdir(parents=True, exist_ok=True)
if source != target:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=ignore)
PY

package_root="${target_dir}"
if [[ "${editable}" == true ]]; then
  package_root="${skill_dir}"
fi
package="${package_root}"
if [[ "${dev}" == true ]]; then
  package="${package_root}[dev]"
fi
pip_args=(-m pip install)
if [[ "${editable}" == true ]]; then
  pip_args+=(-e)
fi
python "${pip_args[@]}" "${package}"
python -m playwright install chromium
python -m daily_intelligence.cli --help >/dev/null
python - "${skill_dir}" "${target_dir}" <<'PY'
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
if source != target:
    for name in ("build", "dist", "daily_intelligence_skill.egg-info"):
        candidate = (target / name).resolve()
        try:
            candidate.relative_to(target)
        except ValueError as exc:
            raise SystemExit(f"Refusing to remove a post-install artifact outside {target}") from exc
        if candidate.is_dir():
            shutil.rmtree(candidate)
        elif candidate.exists():
            candidate.unlink()
PY
printf 'Synchronized skill: %s\n' "${target_dir}"
printf 'Installed daily-intelligence.\n'
