from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.domains.planning import repository as facade
from app.domains.planning import repository_support as support
from app.domains.planning.errors import PlanningContractError
from app.domains.planning.repository import PlanningRepository
from app.domains.task_runtime.xingce_attempt.question_repository import (
    XingceQuestionRepository,
)


REPOSITORY_FILES = (
    "repository.py",
    "repository_support.py",
    "capacity_repository.py",
    "candidate_input_repository.py",
    "essay_snapshot_repository.py",
    "plan_storage_repository.py",
    "execution_repository.py",
)

# These are the repository capabilities consumed across the planning service
# boundary.  Freeze the boundary, not every implementation AST or SQL literal:
# legitimate product work must be able to add evidence and selection queries
# without mechanically refreshing a repository-wide hash snapshot.
REQUIRED_REPOSITORY_CAPABILITIES = {
    "profile",
    "current_or_create_preference",
    "current_or_create_capacity",
    "current_capacity",
    "capacity_version",
    "capacity_by_command",
    "goal_and_ability",
    "objective_snapshot",
    "essay_snapshot",
    "weekly_mock_due",
    "insert_user_capacity",
    "insert_plan",
    "insert_plan_task",
    "current_plan",
    "current_plan_for_fingerprint",
    "plan_tasks",
    "plan_for_capacity",
    "insert_task_execution",
    "current_task_execution",
    "task_execution_by_command",
    "owned_task_for_execution",
    "plan_for_execution",
    "current_task_for_execution",
    "execution_inputs",
    "execution_evidence_exists",
    "active_strategy_decisions",
}


def _trees() -> list[ast.Module]:
    planning = Path(__file__).parents[1] / "app" / "domains" / "planning"
    return [ast.parse((planning / name).read_text()) for name in REPOSITORY_FILES]


def _repository_function_names(trees: list[ast.Module]) -> set[str]:
    names: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assert node.name not in names, f"duplicate repository function: {node.name}"
            names.add(node.name)
    return names


def test_split_preserves_repository_capability_boundary() -> None:
    names = _repository_function_names(_trees())

    assert REQUIRED_REPOSITORY_CAPABILITIES <= names


def test_split_preserves_compatibility_exports_and_transaction_ownership() -> None:
    assert not inspect.signature(PlanningRepository).parameters
    repository = PlanningRepository()
    assert isinstance(repository._question_repository, XingceQuestionRepository)
    assert facade.PlanningContractError is PlanningContractError
    for name in (
        "ProfileSnapshot",
        "PreferenceSnapshot",
        "CapacitySnapshot",
        "GoalAbilitySnapshot",
        "ActiveStrategyDecision",
        "_review_is_learning_eligible",
        "_weakest_dimension",
        "_strict_cause_decision_version_id",
    ):
        assert getattr(facade, name) is getattr(support, name)

    forbidden_transaction_calls = {"begin", "begin_nested", "commit", "flush", "rollback"}
    forbidden_domain = "app.domains.xingce_learning"
    for tree in _trees():
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_transaction_calls
            for node in ast.walk(tree)
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith(forbidden_domain)
            if isinstance(node, ast.Import):
                assert all(not item.name.startswith(forbidden_domain) for item in node.names)
