from __future__ import annotations

import re
from typing import Any

OUTPUT_LANGUAGES = ("zh-CN", "en")

_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_ENGLISH_WORD_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")


def validate_output_language(value: str) -> str:
    """处理：校验输出语言并在不满足约束时报告错误。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    输出：“校验输出语言并在不满足约束时报告错误”得到的规范字符串，供调用方存储、比较或展示。
    """
    language = str(value or "").strip()
    if language not in OUTPUT_LANGUAGES:
        raise ValueError(
            "output.language must be one of: " + ", ".join(OUTPUT_LANGUAGES)
        )
    return language


def is_chinese_output(language: object) -> bool:
    """处理：判断规范化后的输出语言是否为简体中文。
    输入：
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
    return str(language or "zh-CN") == "zh-CN"


def localized(language: object, chinese: str, english: str) -> str:
    """处理：根据目标语言在中英文文本之间选择。
    输入：
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    - ``chinese``：界面或报告字段的中文候选文本。
    - ``english``：界面或报告字段的英文候选文本。
    输出：“根据目标语言在中英文文本之间选择”得到的规范字符串，供调用方存储、比较或展示。
    """
    return chinese if is_chinese_output(language) else english


def translated_title_field(language: object) -> str:
    """处理：返回目标语言对应的译文标题字段名。
    输入：
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    输出：“返回目标语言对应的译文标题字段名”得到的规范字符串，供调用方存储、比较或展示。
    """
    return "title_zh" if is_chinese_output(language) else "title_en"


def translated_title(item: dict[str, Any], language: object) -> str:
    """处理：读取目标语言的译文标题，缺失时回退到规范标题。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    输出：“读取目标语言的译文标题，缺失时回退到规范标题”得到的规范字符串，
      供调用方存储、比较或展示。
    """
    return str(item.get(translated_title_field(language)) or "").strip()


def text_matches_output_language(
    value: object,
    language: object,
    *,
    minimum_units: int = 1,
) -> bool:
    """处理：按最小信息单元判断文本是否符合目标输出语言。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    - ``minimum_units``：判定文本属于目标语言所需的最少有效字符单元数。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
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
    """处理：结合来源语言和标题内容判断是否需要翻译。
    输入：
    - ``source_language``：来源内容声明的语言；与标题文本共同决定是否需要翻译。
    - ``title``：来源提供的标题文本；会清理空白，并用于过滤、身份或展示。
    - ``output_language``：目标报告语言；决定标题译文字段、校验规则和界面文本。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
    source = str(source_language or "").strip().lower()
    if source:
        if is_chinese_output(output_language):
            return source.startswith("zh")
        return source.startswith("en")
    if not is_chinese_output(output_language) and _CJK_PATTERN.search(str(title or "")):
        return False
    return text_matches_output_language(title, output_language)
