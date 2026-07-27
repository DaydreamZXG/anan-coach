from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


GuokaoShenlunQuestionType = Literal[
    "summary",
    "comprehensive_analysis",
    "countermeasure",
    "implementation",
    "composition",
]

GUOKAO_SHENLUN_QUESTION_TYPES: tuple[GuokaoShenlunQuestionType, ...] = (
    "summary",
    "comprehensive_analysis",
    "countermeasure",
    "implementation",
    "composition",
)
GUOKAO_SHENLUN_APPLICABILITY_TAXONOMY_VERSION = (
    "guokao-shenlun-applicability-taxonomy@v1"
)
GUOKAO_SHENLUN_GENERAL_METHOD_VALIDATION_POLICY_VERSION = (
    f"{GUOKAO_SHENLUN_APPLICABILITY_TAXONOMY_VERSION}/unique-category-policy@v1"
)


@dataclass(frozen=True, slots=True)
class GuokaoShenlunCategory:
    code: GuokaoShenlunQuestionType
    display_name: str
    aliases: tuple[str, ...]

    @property
    def applicability_version_ref(self) -> str:
        return (
            f"{GUOKAO_SHENLUN_APPLICABILITY_TAXONOMY_VERSION}/"
            f"{self.code}"
        )


# Order preserves the existing catalog marker precedence for overlapping text.
GUOKAO_SHENLUN_CATEGORIES: tuple[GuokaoShenlunCategory, ...] = (
    GuokaoShenlunCategory(
        code="composition",
        display_name="文章写作",
        aliases=("大作文", "作文", "议论文", "essay"),
    ),
    GuokaoShenlunCategory(
        code="implementation",
        display_name="贯彻执行",
        aliases=("应用文", "公文", "事务性文书"),
    ),
    GuokaoShenlunCategory(
        code="countermeasure",
        display_name="提出对策",
        aliases=("对策题", "对策", "proposal"),
    ),
    GuokaoShenlunCategory(
        code="comprehensive_analysis",
        display_name="综合分析",
        aliases=("分析理解", "解释分析"),
    ),
    GuokaoShenlunCategory(
        code="summary",
        display_name="归纳概括",
        aliases=("概括题", "概括"),
    ),
)

_CATEGORY_BY_CODE = {category.code: category for category in GUOKAO_SHENLUN_CATEGORIES}
_CATEGORY_BY_EXACT_NAME = {
    name.lower(): category
    for category in GUOKAO_SHENLUN_CATEGORIES
    for name in (category.code, category.display_name, *category.aliases)
}


def guokao_shenlun_category(raw: str) -> GuokaoShenlunCategory | None:
    return _CATEGORY_BY_EXACT_NAME.get(raw.strip().lower())


def guokao_shenlun_category_from_catalog_text(
    normalized_text: str,
) -> GuokaoShenlunCategory | None:
    """Match the pre-existing Chinese catalog labels within normalized metadata."""

    lowered = normalized_text.lower()
    for category in GUOKAO_SHENLUN_CATEGORIES:
        chinese_names = (
            category.display_name,
            *(alias for alias in category.aliases if not alias.isascii()),
        )
        if any(name in lowered for name in chinese_names):
            return category
    return None


def unique_guokao_shenlun_applicability(
    values: list[str],
) -> GuokaoShenlunCategory | None:
    if not values:
        return None
    categories: set[GuokaoShenlunQuestionType] = set()
    for value in values:
        category = guokao_shenlun_category(value)
        if category is None:
            return None
        categories.add(category.code)
    if len(categories) != 1:
        return None
    return _CATEGORY_BY_CODE[categories.pop()]


if set(GUOKAO_SHENLUN_QUESTION_TYPES) != set(_CATEGORY_BY_CODE):
    raise RuntimeError("guokao shenlun taxonomy must cover the five database categories")
