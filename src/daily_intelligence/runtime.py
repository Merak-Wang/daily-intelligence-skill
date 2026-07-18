from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import now_iso, read_json, write_json

DATA_ROOT_REGISTRY_SCHEMA = "1.0"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def require_data_root_path(path: Path, data_dir: Path, label: str) -> Path:
    """Reject control/data artifacts that do not belong to the active data root."""
    resolved = path.expanduser().resolve()
    root = data_dir.expanduser().resolve()
    if not _is_relative_to(resolved, root):
        raise ValueError(
            f"{label} is outside the active DAILY_INTEL_DATA_DIR: {resolved}. "
            f"Active root: {root}. Re-run with --data-dir {resolved_data_root_hint(resolved)} "
            "or adopt one canonical root before continuing."
        )
    return resolved


def resolved_data_root_hint(path: Path) -> str:
    """Infer the root above a known artifact directory for an actionable error."""
    artifact_directories = {
        "runs",
        "indexes",
        "context",
        "content",
        "reports",
        "evaluations",
        "state",
        "publishing",
        "locks",
        "challenges",
    }
    for parent in (path, *path.parents):
        if parent.name in artifact_directories:
            return f'"{parent.parent}"'
    return f'"{path.parent}"'


def validate_run_data_root(run: dict[str, Any], run_path: Path, data_dir: Path) -> Path:
    root = data_dir.expanduser().resolve()
    require_data_root_path(run_path, root, "Run manifest")
    recorded = run.get("data_root")
    if recorded and Path(str(recorded)).expanduser().resolve() != root:
        raise ValueError(
            "Run manifest data_root does not match the active DAILY_INTEL_DATA_DIR: "
            f"run={Path(str(recorded)).expanduser().resolve()}, active={root}"
        )
    artifacts = run.get("artifacts", {})
    if isinstance(artifacts, dict):
        for key in (
            "index_path",
            "context_path",
            "json_path",
            "markdown_path",
            "html_path",
            "pdf_path",
            "local_index_path",
        ):
            value = artifacts.get(key)
            if value:
                require_data_root_path(Path(str(value)), root, f"Run artifact {key}")
    return root


def data_root_registry_path(hermes_home: Path) -> Path:
    return hermes_home.expanduser().resolve() / "state" / "daily-intelligence-data-root.json"


def load_bound_data_root(hermes_home: Path) -> Path | None:
    registry_path = data_root_registry_path(hermes_home)
    if not registry_path.exists():
        return None
    payload = read_json(registry_path)
    if not isinstance(payload, dict) or not payload.get("data_root"):
        raise ValueError(f"Invalid daily-intelligence data-root registry: {registry_path}")
    return Path(str(payload["data_root"])).expanduser().resolve()


def bind_data_root(
    data_dir: Path,
    hermes_home: Path,
    *,
    adopt: bool = False,
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Bind one live Hermes installation to one data root.

    External development/test directories are intentionally not registered globally.
    """
    root = data_dir.expanduser().resolve()
    home = hermes_home.expanduser().resolve()
    if not _is_relative_to(root, home):
        return {"status": "external_unbound", "data_root": str(root)}

    registry_path = data_root_registry_path(home)
    previous = load_bound_data_root(home)
    if previous and previous != root and not adopt:
        raise ValueError(
            "Daily Intelligence is already bound to another data root: "
            f"{previous}. Refusing to use {root}. Use `daily-intel --data-dir \"{root}\" "
            "data-root adopt` only after confirming the intended history."
        )
    payload = {
        "schema_version": DATA_ROOT_REGISTRY_SCHEMA,
        "data_root": str(root),
        "updated_at": now_iso(timezone),
        **({"previous_data_root": str(previous)} if previous and previous != root else {}),
    }
    write_json(registry_path, payload)
    return {
        "status": "adopted" if adopt and previous != root else "bound",
        "registry_path": str(registry_path),
        **payload,
    }
