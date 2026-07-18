from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


GuokaoXingceModuleCode = Literal[
    "political_theory",
    "general_knowledge",
    "verbal_comprehension",
    "quantitative_relations",
    "judgment_reasoning",
    "data_analysis",
]

GUOKAO_XINGCE_CATALOG_VERSION = "guokao-xingce-modules-2025-v1"
GUOKAO_XINGCE_LEGACY_CATALOG_VERSION = "guokao-xingce-modules-legacy-five-v1"
GUOKAO_XINGCE_CURRENT_SINCE_EXAM_YEAR = 2025


@dataclass(frozen=True, slots=True)
class GuokaoXingceModuleDefinition:
    code: GuokaoXingceModuleCode
    display_name: str
    aliases: tuple[str, ...]
    vice_provincial_question_count: int
    city_question_count: int
    administrative_law_question_count: int
    placement_question_count: int

    @property
    def structural_weight_points(self) -> float:
        return self.vice_provincial_question_count / 135 * 100


GUOKAO_XINGCE_MODULES: tuple[GuokaoXingceModuleDefinition, ...] = (
    GuokaoXingceModuleDefinition(
        code="political_theory",
        display_name="政治理论",
        aliases=(),
        vice_provincial_question_count=20,
        city_question_count=20,
        administrative_law_question_count=20,
        placement_question_count=4,
    ),
    GuokaoXingceModuleDefinition(
        code="general_knowledge",
        display_name="常识判断",
        aliases=(),
        vice_provincial_question_count=15,
        city_question_count=15,
        administrative_law_question_count=15,
        placement_question_count=3,
    ),
    GuokaoXingceModuleDefinition(
        code="verbal_comprehension",
        display_name="言语理解与表达",
        aliases=("言语理解",),
        vice_provincial_question_count=30,
        city_question_count=30,
        administrative_law_question_count=30,
        placement_question_count=7,
    ),
    GuokaoXingceModuleDefinition(
        code="quantitative_relations",
        display_name="数量关系",
        aliases=(),
        vice_provincial_question_count=15,
        city_question_count=10,
        administrative_law_question_count=10,
        placement_question_count=3,
    ),
    GuokaoXingceModuleDefinition(
        code="judgment_reasoning",
        display_name="判断推理",
        aliases=(),
        vice_provincial_question_count=35,
        city_question_count=35,
        administrative_law_question_count=35,
        placement_question_count=8,
    ),
    GuokaoXingceModuleDefinition(
        code="data_analysis",
        display_name="资料分析",
        aliases=(),
        vice_provincial_question_count=20,
        city_question_count=20,
        administrative_law_question_count=20,
        placement_question_count=5,
    ),
)

GUOKAO_XINGCE_MODULE_NAMES: tuple[str, ...] = tuple(
    module.display_name for module in GUOKAO_XINGCE_MODULES
)
GUOKAO_XINGCE_LEGACY_FIVE_MODULE_NAMES: tuple[str, ...] = (
    "常识判断",
    "言语理解与表达",
    "数量关系",
    "判断推理",
    "资料分析",
)
GUOKAO_XINGCE_VICE_PROVINCIAL_BLUEPRINT: tuple[tuple[str, int], ...] = tuple(
    (module.display_name, module.vice_provincial_question_count)
    for module in GUOKAO_XINGCE_MODULES
)
GUOKAO_XINGCE_CITY_BLUEPRINT: tuple[tuple[str, int], ...] = tuple(
    (module.display_name, module.city_question_count)
    for module in GUOKAO_XINGCE_MODULES
)
GUOKAO_XINGCE_ADMINISTRATIVE_LAW_BLUEPRINT: tuple[tuple[str, int], ...] = tuple(
    (module.display_name, module.administrative_law_question_count)
    for module in GUOKAO_XINGCE_MODULES
)
GUOKAO_XINGCE_PLACEMENT_PLAN: tuple[tuple[str, int, float], ...] = tuple(
    (
        module.display_name,
        module.placement_question_count,
        module.structural_weight_points,
    )
    for module in GUOKAO_XINGCE_MODULES
)

_MODULE_BY_NAME = {
    name: module
    for module in GUOKAO_XINGCE_MODULES
    for name in (module.display_name, *module.aliases)
}


def canonical_guokao_xingce_module_name(raw: str) -> str:
    normalized = raw.strip()
    module = _MODULE_BY_NAME.get(normalized)
    if module is None:
        raise ValueError(f"unsupported guokao xingce module: {raw}")
    return module.display_name


def guokao_xingce_module_code(raw: str) -> GuokaoXingceModuleCode:
    normalized = canonical_guokao_xingce_module_name(raw)
    return _MODULE_BY_NAME[normalized].code


if len(GUOKAO_XINGCE_MODULES) != 6:
    raise RuntimeError("current guokao xingce catalog must contain six modules")
if sum(count for _name, count in GUOKAO_XINGCE_VICE_PROVINCIAL_BLUEPRINT) != 135:
    raise RuntimeError("vice-provincial guokao blueprint must contain 135 questions")
if sum(count for _name, count, _weight in GUOKAO_XINGCE_PLACEMENT_PLAN) != 30:
    raise RuntimeError("guokao placement plan must contain 30 questions")
