from __future__ import annotations

from enum import StrEnum

from .localization import is_chinese_output


class ContentModule(StrEnum):
    """处理：定义正文模块的可用枚举值。
    输入：
    - 无显式业务参数：不声明额外构造字段；该定义以 ``StrEnum`` 为基础，
      通过类成员承担“定义正文模块的可用枚举值”职责。
    输出：构造后的 ``ContentModule`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    INFORMATION = "information"
    TECHNOLOGY = "technology"


class InformationCategory(StrEnum):
    """处理：定义信息领域报告允许使用的规范分类 ID。
    输入：
    - 无显式业务参数：不声明额外构造字段；该定义以 ``StrEnum`` 为基础，
      通过类成员承担“定义信息领域报告允许使用的规范分类 ID”职责。
    输出：构造后的 ``InformationCategory`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    INTERNATIONAL = "international"
    DOMESTIC = "domestic"
    MILITARY = "military"
    MARKET = "market"
    # 旧分类仍需读取 schema 1.1/1.2 报告和来源索引，但新输出只使用规范值。
    ECONOMY = "economy"
    TECHNOLOGY = "technology"


class TechnologyCategory(StrEnum):
    """处理：定义技术领域报告允许使用的规范分类 ID。
    输入：
    - 无显式业务参数：不声明额外构造字段；该定义以 ``StrEnum`` 为基础，
      通过类成员承担“定义技术领域报告允许使用的规范分类 ID”职责。
    输出：构造后的 ``TechnologyCategory`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    NEWS = "news"
    PAPERS = "papers"
    OPEN_SOURCE = "open_source"


class AnalysisDomain(StrEnum):
    """处理：定义分析领域的可用枚举值。
    输入：
    - 无显式业务参数：不声明额外构造字段；该定义以 ``StrEnum`` 为基础，
      通过类成员承担“定义分析领域的可用枚举值”职责。
    输出：构造后的 ``AnalysisDomain`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    GEOPOLITICS = "geopolitics"
    MARKETS = "markets"
    AI_TECHNOLOGY = "ai_technology"


CATEGORIES_BY_MODULE: dict[str, set[str]] = {
    ContentModule.INFORMATION: {item.value for item in InformationCategory},
    ContentModule.TECHNOLOGY: {item.value for item in TechnologyCategory},
}

REQUIRED_SECTION_IDS_V11 = {
    "information.international",
    "information.domestic",
    "information.military",
    "information.economy",
    "technology.news",
    "technology.papers",
    "technology.open_source",
}
REQUIRED_SECTION_IDS_V12 = REQUIRED_SECTION_IDS_V11 | {"information.technology"}
REQUIRED_SECTION_IDS_V13 = {
    "information.international",
    "information.domestic",
    "information.military",
    "information.market",
    "technology.news",
    "technology.papers",
    "technology.open_source",
}

SECTION_ORDER_V13 = (
    "information.international",
    "information.domestic",
    "information.military",
    "information.market",
    "technology.news",
    "technology.papers",
    "technology.open_source",
)

SECTION_GROUPS_V13 = {
    "information": SECTION_ORDER_V13[:4],
    "technology": SECTION_ORDER_V13[4:],
}

SECTION_TITLES_V13 = {
    "information.international": "国际",
    "information.domestic": "国内新闻",
    "information.military": "军事",
    "information.market": "市场",
    "technology.news": "技术新闻",
    "technology.papers": "值得阅读的论文",
    "technology.open_source": "今日值得关注的开源项目",
}

SECTION_TITLES_EN_V13 = {
    "information.international": "International",
    "information.domestic": "Domestic News",
    "information.military": "Military",
    "information.market": "Markets",
    "technology.news": "Technology News",
    "technology.papers": "Papers Worth Reading",
    "technology.open_source": "Open-Source Projects to Watch",
}


def section_titles(language: object) -> dict[str, str]:
    """处理：按输出语言返回规范栏目 ID 到标题的映射。
    输入：
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    输出：“按输出语言返回规范栏目 ID 到标题的映射”形成的结构化字典；
      键值表达该处理定义的业务记录或查找关系。
    """
    return SECTION_TITLES_V13 if is_chinese_output(language) else SECTION_TITLES_EN_V13

# 早期模型草稿使用过这些直观名称：只在草稿边界兼容接收，持久化时一律编译为
# 上方 schema 1.5 的规范标识。
SECTION_ID_ALIASES_V15 = {
    "information.economy": "information.market",
    "information.markets": "information.market",
    "technology.tech_news": "technology.news",
    "technology.technology_news": "technology.news",
    "technology.oss": "technology.open_source",
    "technology.opensource": "technology.open_source",
}


def canonical_section_id(section_id: str) -> str:
    """处理：把规范或旧版栏目名称映射到当前 schema ID。
    输入：
    - ``section_id``：待规范化的报告栏目 ID 或兼容别名。
    输出：可跨修订关联的稳定字符串标识，供索引、状态或发布记录使用。
    """
    normalized = section_id.strip()
    return SECTION_ID_ALIASES_V15.get(normalized, normalized)


def required_section_ids(schema_version: str | None) -> set[str]:
    """处理：返回指定报告 schema 强制要求的栏目 ID。
    输入：
    - ``schema_version``：报告 schema 版本；决定必须存在的栏目集合。
    输出：封装“返回指定报告 schema 强制要求的栏目 ID”业务结果的 ``set[str]`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    if schema_version in {"1.3", "1.4", "1.5", "2.0"}:
        return REQUIRED_SECTION_IDS_V13
    return REQUIRED_SECTION_IDS_V12 if schema_version == "1.2" else REQUIRED_SECTION_IDS_V11


def validate_content_taxonomy(module: str, category: str) -> None:
    """处理：校验模块与栏目 ID 是否属于当前报告分类契约。
    输入：
    - ``module``：报告顶层领域 ID，例如 information 或 technology。
    - ``category``：报告栏目 ID；必须与 module 和当前 taxonomy 契约一致。
    输出：不返回新数据；完成“校验模块与栏目 ID 是否属于当前报告分类契约”，
      副作用限于该处理声明的受控对象或产物。
    """
    allowed = CATEGORIES_BY_MODULE.get(module)
    if allowed is None:
        raise ValueError(f"Unknown content module: {module}")
    if category not in allowed:
        raise ValueError(f"Category {category!r} is not valid for module {module!r}")
