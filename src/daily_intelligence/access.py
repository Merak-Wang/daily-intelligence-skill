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
    """处理：统一识别 HTTP 与浏览器采集中的访问失败类型。
    输入：
    - ``http_status``：页面最近一次 HTTP 状态码；无网络响应时可为空。
    - ``title``：来源提供的标题文本；会清理空白，并用于过滤、身份或展示。
    - ``body``：正文或 HTML 文本；保存前只作为数据处理。
    - ``iframe_detected``：访问分类器是否在页面中发现验证码或登录挑战 iframe。
    输出：“统一识别 HTTP 与浏览器采集中的访问失败类型”形成的结构化字典；
      典型键包括 iframe_detected、matched_text、rate_limited、required。
    """
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
