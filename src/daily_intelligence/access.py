from __future__ import annotations

from typing import Any

CHALLENGE_TEXTS = (
    "verify you are human",
    "checking your browser",
    "are you a robot",
    "unusual traffic",
    "access denied",
    "security check",
    "enable javascript and cookies",
    "captcha",
    "robot check",
)
RATE_LIMIT_TEXTS = (
    "temporarily limited",
    "temporarily restricted",
    "too many requests",
    "rate limit exceeded",
)


def classify_access_text(
    http_status: int | None,
    title: str,
    body: str,
    *,
    iframe_detected: bool = False,
) -> dict[str, Any]:
    """Classify access failures consistently for HTTP and browser collection."""
    haystack_title = title.lower()
    haystack_body = body.lower()
    rate_limited_text = next(
        (
            text
            for text in RATE_LIMIT_TEXTS
            if text in haystack_title or text in haystack_body
        ),
        None,
    )
    matched = rate_limited_text or next(
        (
            text
            for text in CHALLENGE_TEXTS
            if text in haystack_title or text in haystack_body
        ),
        None,
    )
    rate_limited = http_status == 429 or rate_limited_text is not None
    required = (
        http_status in {401, 403, 429} or matched is not None or iframe_detected
    )
    return {
        "required": required,
        "rate_limited": rate_limited,
        "matched_text": matched,
        "iframe_detected": iframe_detected,
    }
