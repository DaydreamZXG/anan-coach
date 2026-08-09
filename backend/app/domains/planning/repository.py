from __future__ import annotations

from app.domains.planning.capacity_repository import CapacityRepositoryMixin
from app.domains.planning.candidate_input_repository import CandidateInputRepositoryMixin
from app.domains.planning.errors import PlanningContractError as PlanningContractError
from app.domains.planning.essay_snapshot_repository import EssaySnapshotRepositoryMixin
from app.domains.planning.execution_repository import ExecutionRepositoryMixin
from app.domains.planning.plan_storage_repository import PlanStorageRepositoryMixin
from app.domains.planning.repository_support import (
    ActiveStrategyDecision as ActiveStrategyDecision,
    CapacitySnapshot as CapacitySnapshot,
    GoalAbilitySnapshot as GoalAbilitySnapshot,
    PreferenceSnapshot as PreferenceSnapshot,
    ProfileSnapshot as ProfileSnapshot,
    _as_utc as _as_utc,
    _capacity_snapshot as _capacity_snapshot,
    _essay_closure_context_gap as _essay_closure_context_gap,
    _essay_closure_gap as _essay_closure_gap,
    _fingerprint as _fingerprint,
    _optional_float as _optional_float,
    _optional_str as _optional_str,
    _preference_snapshot as _preference_snapshot,
    _review_is_learning_eligible as _review_is_learning_eligible,
    _strict_cause_decision_version_id as _strict_cause_decision_version_id,
    _weakest_canonical_module as _weakest_canonical_module,
    _weakest_dimension as _weakest_dimension,
    _weakest_dimension_key as _weakest_dimension_key,
)
from app.domains.task_runtime.xingce_attempt.question_repository import (
    XingceQuestionRepository,
)


class PlanningRepository(
    CapacityRepositoryMixin,
    CandidateInputRepositoryMixin,
    EssaySnapshotRepositoryMixin,
    PlanStorageRepositoryMixin,
    ExecutionRepositoryMixin,
):
    def __init__(self) -> None:
        self._question_repository = XingceQuestionRepository()
