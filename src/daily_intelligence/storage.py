from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .utils import write_json

_REVISION_RE = re.compile(r"-r(\d+)\.json$")


def next_revision(directory: Path, stem: str) -> int:
    revisions: list[int] = []
    if directory.exists():
        for path in directory.glob(f"{stem}-r*.json"):
            match = _REVISION_RE.search(path.name)
            if match:
                revisions.append(int(match.group(1)))
    return max(revisions, default=0) + 1


def write_immutable_json(path: Path, data: object) -> Path:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite immutable artifact: {path}")
    return write_json(path, data)


def write_text_atomic(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


@contextmanager
def exclusive_lock(path: Path, payload: dict) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError as exc:
        raise RuntimeError(
            f"Another run holds {path}. Remove it only after confirming no run is active."
        ) from exc
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
