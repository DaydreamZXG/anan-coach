from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "quarantine_incomplete_questions.py"
SPEC = importlib.util.spec_from_file_location("quarantine_incomplete_questions", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _facts(*, status: str = "active") -> tuple:
    rows = []
    for index in range(MODULE.EXPECTED_MISSING_HEAD):
        rows.append(
            MODULE.QuestionFact(
                question_id=f"missing-head-{index:03d}",
                status=status,
                missing_content_head=True,
                missing_answer=False,
                source_dataset_ids=(MODULE.DATASETS[0],),
            )
        )
    for index in range(MODULE.EXPECTED_MISSING_ANSWER):
        rows.append(
            MODULE.QuestionFact(
                question_id=f"missing-answer-{index:03d}",
                status=status,
                missing_content_head=False,
                missing_answer=True,
                source_dataset_ids=(MODULE.DATASETS[1],),
            )
        )
    return tuple(rows)


def test_exact_frozen_baseline_produces_deterministic_plan() -> None:
    first = MODULE.build_plan(_facts(), allowed_statuses={"active"})
    second = MODULE.build_plan(reversed(_facts()), allowed_statuses={"active"})

    assert first.counts == {
        "questions": 273,
        "missingContentHead": 153,
        "missingAnswer": 120,
        "overlap": 0,
        "userReferencedQuestions": 0,
        "eligibleBindingQuestions": 0,
    }
    assert first.fingerprint == second.fingerprint
    assert [row.question_id for row in first.rows] == sorted(
        row.question_id for row in first.rows
    )


@pytest.mark.parametrize(
    "rows",
    (
        lambda: _facts()[:-1],
        lambda: _facts() + (_facts()[0],),
        lambda: (replace(_facts()[0], missing_answer=True),) + _facts()[1:],
        lambda: (replace(_facts()[0], source_dataset_ids=("other",)),) + _facts()[1:],
        lambda: (replace(_facts()[0], has_assessment=True),) + _facts()[1:],
        lambda: (replace(_facts()[0], has_exposure=True),) + _facts()[1:],
        lambda: (replace(_facts()[0], has_favorite=True),) + _facts()[1:],
        lambda: (replace(_facts()[0], has_eligible_binding=True),) + _facts()[1:],
    ),
)
def test_count_reason_scope_reference_or_binding_drift_stops(rows) -> None:
    with pytest.raises(MODULE.QuarantineError):
        MODULE.build_plan(rows(), allowed_statuses={"active"})


def test_apply_state_is_all_active_or_all_quarantined_only() -> None:
    active = MODULE.build_plan(_facts(), allowed_statuses={"active"})
    quarantined = MODULE.build_plan(
        _facts(status="quarantined"), allowed_statuses={"quarantined"}
    )

    assert MODULE.replay_state(active.rows) == "applied"
    assert MODULE.replay_state(quarantined.rows) == "unchanged"

    mixed = (replace(active.rows[0], status="quarantined"),) + active.rows[1:]
    with pytest.raises(MODULE.QuarantineError, match="partial/mixed"):
        MODULE.replay_state(mixed)


def test_plan_fingerprint_binds_reason_status_and_ids() -> None:
    plan = MODULE.build_plan(_facts(), allowed_statuses={"active"})

    changed_reason = (replace(plan.rows[0], missing_content_head=True),) + plan.rows[1:]
    changed_status = (replace(plan.rows[0], status="quarantined"),) + plan.rows[1:]
    changed_id = (replace(plan.rows[0], question_id="different"),) + plan.rows[1:]

    assert MODULE.plan_fingerprint(changed_reason) != plan.fingerprint
    assert MODULE.plan_fingerprint(changed_status) != plan.fingerprint
    assert MODULE.plan_fingerprint(changed_id) != plan.fingerprint


def test_operational_sql_mutates_only_question_status() -> None:
    normalized = " ".join(MODULE.UPDATE_SQL.lower().split())
    assert normalized.startswith("update product.questions set status = 'quarantined'")
    assert "where question_id = any" in normalized
    assert "and status = 'active'" in normalized
    assert "delete" not in normalized
    assert "insert" not in normalized
