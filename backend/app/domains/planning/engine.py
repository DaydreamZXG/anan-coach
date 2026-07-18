from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Literal


PlanTrack = Literal["xingce", "essay", "recovery", "mock"]

GOAL_GAIN_WEIGHT = 0.30
ABILITY_GAIN_WEIGHT = 0.30
VERIFICATION_URGENCY_WEIGHT = 0.25
TIME_FIT_WEIGHT = 0.15
LOAD_PENALTY_WEIGHT = 0.15
TRACK_BALANCE_MAX_BONUS = 0.12
DUAL_TRACK_MINIMUM_MINUTES = 40
MAX_DAILY_TASKS = 3


@dataclass(frozen=True)
class PriorityBreakdown:
    goal_gain: float
    ability_gain: float
    verification_urgency: float
    time_fit: float
    load_penalty: float

    def __post_init__(self) -> None:
        for field_name, value in self.__dict__.items():
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class TaskCandidate:
    candidate_key: str
    track: PlanTrack
    stage: str
    kind: str
    title: str
    reason: str
    estimated_minutes: int
    completion_rule: str
    start_url: str
    evidence_refs: tuple[dict[str, str], ...]
    priority: PriorityBreakdown
    mandatory: bool = False
    strategy_module_keys: tuple[str, ...] = ()
    strategy_question_type: str | None = None

    def __post_init__(self) -> None:
        if not self.candidate_key.strip():
            raise ValueError("candidate_key is required")
        if self.estimated_minutes <= 0:
            raise ValueError("estimated_minutes must be positive")
        if not self.title.strip() or not self.reason.strip() or not self.completion_rule.strip():
            raise ValueError("candidate text fields are required")
        if not self.start_url.startswith("/"):
            raise ValueError("start_url must be an internal absolute path")
        if any(not value.strip() for value in self.strategy_module_keys):
            raise ValueError("strategy module keys cannot contain blank values")
        if (
            self.strategy_question_type is not None
            and not self.strategy_question_type.strip()
        ):
            raise ValueError("strategy question type cannot be blank")


@dataclass(frozen=True)
class RankedCandidate:
    candidate: TaskCandidate
    priority_score: float
    track_balance_bonus: float


@dataclass(frozen=True)
class PlanSelection:
    tasks: tuple[RankedCandidate, ...]
    scheduled_minutes: int
    considered_tracks: tuple[PlanTrack, ...]
    dual_track_constraint_applied: bool
    explanation_codes: tuple[str, ...]


def select_daily_tasks(
    candidates: list[TaskCandidate],
    *,
    available_minutes: int,
    recent_track_minutes: dict[PlanTrack, int] | None = None,
    recovery_day: bool = False,
) -> PlanSelection:
    if available_minutes < 0:
        raise ValueError("available_minutes cannot be negative")
    keys = [item.candidate_key for item in candidates]
    if len(keys) != len(set(keys)):
        raise ValueError("candidate keys must be unique")
    recent = recent_track_minutes or {}
    ranked = [_rank(item, recent) for item in candidates]
    ranked.sort(key=_rank_key)
    considered_tracks = tuple(dict.fromkeys(item.candidate.track for item in ranked))
    if not ranked or available_minutes == 0:
        return PlanSelection((), 0, considered_tracks, False, ("no_schedulable_capacity",))

    mandatory = [item for item in ranked if item.candidate.mandatory]
    if mandatory:
        chosen = _fit_in_order(mandatory, available_minutes, maximum=1)
        if chosen:
            return PlanSelection(
                tasks=tuple(chosen),
                scheduled_minutes=_minutes(chosen),
                considered_tracks=considered_tracks,
                dual_track_constraint_applied=False,
                explanation_codes=("mandatory_calibration_first",),
            )
        ranked = [item for item in ranked if not item.candidate.mandatory]
        if not ranked:
            return PlanSelection(
                tasks=(),
                scheduled_minutes=0,
                considered_tracks=considered_tracks,
                dual_track_constraint_applied=False,
                explanation_codes=("mandatory_task_capacity_insufficient",),
            )

    if recovery_day:
        recovery_candidates = [
            item
            for item in ranked
            if item.candidate.stage
            in {
                "objective_reflection",
                "objective_verification",
                "essay_review",
                "essay_rewrite",
            }
            and item.candidate.priority.verification_urgency >= 0.7
        ]
        recovery_budget = min(available_minutes, 20)
        chosen = _fit_in_order(recovery_candidates, recovery_budget, maximum=1)
        return PlanSelection(
            tasks=tuple(chosen),
            scheduled_minutes=_minutes(chosen),
            considered_tracks=considered_tracks,
            dual_track_constraint_applied=False,
            explanation_codes=("recovery_day_no_new_learning",),
        )

    chosen: list[RankedCandidate] = []
    dual_track_applied = False
    if available_minutes >= DUAL_TRACK_MINIMUM_MINUTES:
        pair = _best_dual_track_pair(ranked, available_minutes)
        if pair is not None:
            chosen.extend(pair)
            dual_track_applied = True

    if not chosen:
        chosen = _fit_in_order(ranked, available_minutes, maximum=1)

    used_keys = {item.candidate.candidate_key for item in chosen}
    remaining_budget = available_minutes - _minutes(chosen)
    if remaining_budget > 0 and len(chosen) < MAX_DAILY_TASKS:
        remainder = [
            item for item in ranked if item.candidate.candidate_key not in used_keys
        ]
        chosen.extend(
            _fit_in_order(
                remainder,
                remaining_budget,
                maximum=MAX_DAILY_TASKS - len(chosen),
            )
        )

    chosen.sort(key=_rank_key)
    codes = ["evidence_weighted_priority"]
    if mandatory:
        codes.append("mandatory_task_capacity_insufficient")
    if dual_track_applied:
        codes.append("xingce_essay_dual_track")
    if any(item.track_balance_bonus > 0 for item in chosen):
        codes.append("rolling_track_balance")
    return PlanSelection(
        tasks=tuple(chosen),
        scheduled_minutes=_minutes(chosen),
        considered_tracks=considered_tracks,
        dual_track_constraint_applied=dual_track_applied,
        explanation_codes=tuple(codes),
    )


def _rank(
    candidate: TaskCandidate,
    recent_track_minutes: dict[PlanTrack, int],
) -> RankedCandidate:
    priority = candidate.priority
    base = (
        priority.goal_gain * GOAL_GAIN_WEIGHT
        + priority.ability_gain * ABILITY_GAIN_WEIGHT
        + priority.verification_urgency * VERIFICATION_URGENCY_WEIGHT
        + priority.time_fit * TIME_FIT_WEIGHT
        - priority.load_penalty * LOAD_PENALTY_WEIGHT
    )
    balance_bonus = _track_balance_bonus(candidate.track, recent_track_minutes)
    return RankedCandidate(
        candidate=candidate,
        priority_score=round(max(0.0, min(1.0, base + balance_bonus)), 4),
        track_balance_bonus=balance_bonus,
    )


def _track_balance_bonus(
    track: PlanTrack,
    recent_track_minutes: dict[PlanTrack, int],
) -> float:
    if track not in {"xingce", "essay"}:
        return 0.0
    xingce = max(0, recent_track_minutes.get("xingce", 0))
    essay = max(0, recent_track_minutes.get("essay", 0))
    total = xingce + essay
    if total == 0:
        return 0.0
    own = xingce if track == "xingce" else essay
    share = own / total
    if share >= 0.45:
        return 0.0
    return round(min(TRACK_BALANCE_MAX_BONUS, (0.45 - share) * 0.4), 4)


def _best_dual_track_pair(
    ranked: list[RankedCandidate],
    available_minutes: int,
) -> tuple[RankedCandidate, RankedCandidate] | None:
    xingce = [item for item in ranked if item.candidate.track == "xingce"]
    essay = [item for item in ranked if item.candidate.track == "essay"]
    feasible = [
        pair
        for pair in product(xingce, essay)
        if _minutes(pair) <= available_minutes
    ]
    if not feasible:
        return None
    return max(
        feasible,
        key=lambda pair: (
            round(sum(item.priority_score for item in pair), 4),
            -_minutes(pair),
            tuple(item.candidate.candidate_key for item in pair),
        ),
    )


def _fit_in_order(
    ranked: list[RankedCandidate],
    available_minutes: int,
    *,
    maximum: int,
) -> list[RankedCandidate]:
    chosen: list[RankedCandidate] = []
    remaining = available_minutes
    for item in ranked:
        if len(chosen) >= maximum:
            break
        if item.candidate.estimated_minutes > remaining:
            continue
        chosen.append(item)
        remaining -= item.candidate.estimated_minutes
    return chosen


def _rank_key(item: RankedCandidate) -> tuple[float, float, str]:
    return (
        -item.priority_score,
        -item.candidate.priority.verification_urgency,
        item.candidate.candidate_key,
    )


def _minutes(items: tuple[RankedCandidate, ...] | list[RankedCandidate]) -> int:
    return sum(item.candidate.estimated_minutes for item in items)
