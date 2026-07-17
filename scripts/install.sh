#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
editable=false
dev=false
for arg in "$@"; do
  case "${arg}" in
    --editable) editable=true ;;
    --dev) dev=true ;;
    *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

package="${skill_dir}"
if [[ "${dev}" == true ]]; then
  package="${skill_dir}[dev]"
fi
pip_args=(-m pip install)
if [[ "${editable}" == true ]]; then
  pip_args+=(-e)
fi
python "${pip_args[@]}" "${package}"
python -m playwright install chromium
python -m daily_intelligence.cli --help >/dev/null
printf 'Installed daily-intelligence.
'
