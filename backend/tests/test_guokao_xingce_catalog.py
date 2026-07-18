from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.guokao_xingce import (
    GUOKAO_XINGCE_ADMINISTRATIVE_LAW_BLUEPRINT,
    GUOKAO_XINGCE_CATALOG_VERSION,
    GUOKAO_XINGCE_CITY_BLUEPRINT,
    GUOKAO_XINGCE_MODULE_NAMES,
    GUOKAO_XINGCE_PLACEMENT_PLAN,
    GUOKAO_XINGCE_VICE_PROVINCIAL_BLUEPRINT,
    canonical_guokao_xingce_module_name,
    guokao_xingce_module_code,
)
from app.domains.mock_exam.schemas import MockExamModuleReview
from app.domains.mock_exam.service import _performance_index, _published_module_names


def test_current_guokao_catalog_is_the_published_six_module_contract() -> None:
    assert GUOKAO_XINGCE_CATALOG_VERSION == "guokao-xingce-modules-2025-v1"
    assert GUOKAO_XINGCE_MODULE_NAMES == (
        "政治理论",
        "常识判断",
        "言语理解与表达",
        "数量关系",
        "判断推理",
        "资料分析",
    )
    assert dict(GUOKAO_XINGCE_VICE_PROVINCIAL_BLUEPRINT) == {
        "政治理论": 20,
        "常识判断": 15,
        "言语理解与表达": 30,
        "数量关系": 15,
        "判断推理": 35,
        "资料分析": 20,
    }
    assert sum(count for _module, count in GUOKAO_XINGCE_VICE_PROVINCIAL_BLUEPRINT) == 135
    assert sum(count for _module, count in GUOKAO_XINGCE_CITY_BLUEPRINT) == 130
    assert sum(count for _module, count in GUOKAO_XINGCE_ADMINISTRATIVE_LAW_BLUEPRINT) == 130


def test_placement_plan_covers_every_current_module_without_fake_history() -> None:
    assert tuple(module for module, _count, _weight in GUOKAO_XINGCE_PLACEMENT_PLAN) == (
        GUOKAO_XINGCE_MODULE_NAMES
    )
    assert sum(count for _module, count, _weight in GUOKAO_XINGCE_PLACEMENT_PLAN) == 30
    assert sum(weight for _module, _count, weight in GUOKAO_XINGCE_PLACEMENT_PLAN) == pytest.approx(
        100.0
    )


def test_only_declared_aliases_are_normalized() -> None:
    assert canonical_guokao_xingce_module_name("言语理解") == "言语理解与表达"
    assert guokao_xingce_module_code("政治理论") == "political_theory"
    with pytest.raises(ValueError, match="unsupported guokao xingce module"):
        canonical_guokao_xingce_module_name("常识")


def test_legacy_five_module_policy_remains_computable_from_frozen_evidence() -> None:
    names = _published_module_names(
        [
            {"moduleName": module, "questionCount": 20}
            for module in (
                "常识判断",
                "言语理解与表达",
                "数量关系",
                "判断推理",
                "资料分析",
            )
        ]
    )
    modules = [
        MockExamModuleReview(
            moduleName=module,
            correct=10,
            total=20,
            accuracy=0.5,
            changeFromPrevious=None,
        )
        for module in names
    ]
    score, interval = _performance_index(
        modules,
        module_weights={module: 0.2 for module in names},
    )
    assert score == 50.0
    assert interval.lower < score < interval.upper


def test_six_module_scoring_policy_is_published_without_mutating_history(
    db_session: Session,
) -> None:
    rows = db_session.execute(
        text(
            """
            select scoring_policy_version_id, state, metric_name, module_weights
            from product.mock_exam_scoring_policy_versions
            where scoring_policy_version_id in (
              'mock-balanced-performance-index-v1',
              'mock-question-weighted-performance-index-v2'
            )
            order by scoring_policy_version_id
            """
        )
    ).mappings().all()
    by_id = {str(row["scoring_policy_version_id"]): row for row in rows}
    assert by_id["mock-balanced-performance-index-v1"]["state"] == "published"
    current = by_id["mock-question-weighted-performance-index-v2"]
    assert current["state"] == "published"
    assert current["metric_name"] == "question_weighted_performance_index"
    weights = {str(key): float(value) for key, value in current["module_weights"].items()}
    assert set(weights) == set(GUOKAO_XINGCE_MODULE_NAMES)
    assert sum(weights.values()) == pytest.approx(1.0)
