from __future__ import annotations

import re
from typing import Any

OUTPUT_LANGUAGES = ("zh-CN", "en")

_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_ENGLISH_WORD_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")


def validate_output_language(value: str) -> str:
    language = str(value or "").strip()
    if language not in OUTPUT_LANGUAGES:
        raise ValueError(
            "output.language must be one of: " + ", ".join(OUTPUT_LANGUAGES)
        )
    return language


def is_chinese_output(language: object) -> bool:
    return str(language or "zh-CN") == "zh-CN"


def localized(language: object, chinese: str, english: str) -> str:
    return chinese if is_chinese_output(language) else english


def translated_title_field(language: object) -> str:
    return "title_zh" if is_chinese_output(language) else "title_en"


def translated_title(item: dict[str, Any], language: object) -> str:
    return str(item.get(translated_title_field(language)) or "").strip()


def text_matches_output_language(
    value: object,
    language: object,
    *,
    minimum_units: int = 1,
) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if is_chinese_output(language):
        return len(_CJK_PATTERN.findall(text)) >= minimum_units
    return len(_ENGLISH_WORD_PATTERN.findall(text)) >= minimum_units


def source_matches_output_language(
    source_language: object,
    title: object,
    output_language: object,
) -> bool:
    source = str(source_language or "").strip().lower()
    if source:
        if is_chinese_output(output_language):
            return source.startswith("zh")
        return source.startswith("en")
    if not is_chinese_output(output_language) and _CJK_PATTERN.search(str(title or "")):
        return False
    return text_matches_output_language(title, output_language)
