from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import unquote, urljoin, urlsplit

_PLACEHOLDER_MARKERS = (
    "placeholder",
    "default-image",
    "default_image",
    "defaultimage",
    "image-not-found",
    "image_not_found",
    "missing-image",
    "missing_image",
    "no-image",
    "no_image",
    "noimage",
)
_PLACEHOLDER_FILENAMES = {
    "blank.gif",
    "blank.png",
    "spacer.gif",
    "spacer.png",
    "transparent.gif",
    "transparent.png",
}


def is_placeholder_image_url(value: object) -> bool:
    """Return whether a URL advertises an obvious non-content image."""
    text = str(value or "").strip()
    if not text:
        return False
    parsed = urlsplit(text)
    path = unquote(parsed.path).replace("\\", "/").casefold()
    filename = path.rsplit("/", 1)[-1]
    return filename in _PLACEHOLDER_FILENAMES or any(
        marker in path for marker in _PLACEHOLDER_MARKERS
    )


def normalize_image_candidates(
    values: Iterable[object],
    base_url: str = "",
) -> list[str]:
    """Resolve, filter, and stably deduplicate untrusted image candidates."""
    candidates: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        resolved = urljoin(base_url, raw)
        parsed = urlsplit(resolved)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or is_placeholder_image_url(resolved)
            or resolved in seen
        ):
            continue
        seen.add(resolved)
        candidates.append(resolved)
    return candidates


def srcset_candidates(value: object) -> list[str]:
    """Return srcset URLs from largest/last declaration to smallest/first."""
    entries = []
    for entry in str(value or "").split(","):
        candidate = entry.strip().split()
        if candidate:
            entries.append(candidate[0])
    return list(reversed(entries))
