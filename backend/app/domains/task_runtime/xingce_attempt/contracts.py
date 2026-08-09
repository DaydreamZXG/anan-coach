from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Any


XINGCE_QUICK_QUESTION_BUNDLE_SIZE = 3
XINGCE_MAX_QUESTION_BUNDLE_SIZE = 30
XINGCE_MINUTES_PER_QUESTION = 2

# Stored inside the server-private scoring bundle.  Absence is the only
# historical-session discriminator; response projections are replay data and
# must never decide whether compatibility completion runs.
XINGCE_SESSION_GENERATION_KEY = "sessionGeneration"
XINGCE_SESSION_GENERATION_V1 = "xingce-plan-completion-at-submit@v1"
XINGCE_SELECTION_INTENT_V1 = "xingce-selection-intent@v1"
XINGCE_SELECTION_RESULT_V1 = "xingce-selection-result@v1"


class XingceSessionGenerationError(ValueError):
    """Raised when a stored session claims an unsupported generation."""


class XingceSelectionIntentError(ValueError):
    pass


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NODE_FIELDS = {"releaseId", "nodeId", "nodeType", "tagType", "tagValue"}
_CAUSE_FIELDS = {
    "causeCode",
    "focus",
    "questionType",
    "confirmedCauseVersionId",
    "decisionVersionId",
    "candidateVersionId",
}
_FALLBACK_REASONS = {
    None,
    "cold_start_no_grounded_evidence",
    "insufficient_knowledge_point_evidence",
    "knowledge_node_unavailable",
}
_UNKNOWN_DIFFICULTY_REASONS = {
    "missing_shadow_estimate",
    "insufficient_shadow_sample",
}


def freeze_selection_intent(value: object) -> dict[str, Any]:
    """Attach the stable identity derived from every immutable intent field."""

    if not isinstance(value, dict) or "intentId" in value:
        raise XingceSelectionIntentError("unfrozen selection intent is malformed")
    frozen = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    frozen["intentId"] = _selection_intent_identity(frozen)
    return validate_selection_intent(frozen)


def validate_selection_intent(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise XingceSelectionIntentError("selection intent must be an object")
    required = {
        "contractVersion",
        "intentId",
        "knowledgeRelease",
        "targetModule",
        "targetKnowledgeNode",
        "targetMicroSkillNode",
        "confirmedCauseFocus",
        "difficultyPolicy",
        "noveltyPolicy",
        "fallbackReason",
        "evidenceRefs",
    }
    if set(value) != required:
        raise XingceSelectionIntentError("selection intent fields changed")
    if value["contractVersion"] != XINGCE_SELECTION_INTENT_V1:
        raise XingceSelectionIntentError("selection intent version is unsupported")
    if not isinstance(value["targetModule"], str) or not value["targetModule"].strip():
        raise XingceSelectionIntentError("selection target module is required")
    if not isinstance(value["intentId"], str) or not _SHA256_RE.fullmatch(
        value["intentId"]
    ):
        raise XingceSelectionIntentError("selection intent id is malformed")
    for key, expected_type in (
        ("targetKnowledgeNode", "knowledge_point"),
        ("targetMicroSkillNode", "micro_skill"),
    ):
        _validate_selection_node(value[key], key=key, expected_type=expected_type)
    release = value["knowledgeRelease"]
    if not isinstance(release, dict) or set(release) != {
        "releaseId",
        "releaseVersion",
        "sourceDataVersion",
        "vocabVersion",
        "freshUntil",
    } or any(not isinstance(item, str) or not item.strip() for item in release.values()):
        raise XingceSelectionIntentError("selection knowledge release is malformed")
    try:
        fresh_until = datetime.fromisoformat(release["freshUntil"])
    except ValueError as exc:
        raise XingceSelectionIntentError(
            "selection knowledge release freshness is malformed"
        ) from exc
    if fresh_until.tzinfo is None:
        raise XingceSelectionIntentError(
            "selection knowledge release freshness is malformed"
        )
    for key in ("targetKnowledgeNode", "targetMicroSkillNode"):
        if value[key] is not None and value[key]["releaseId"] != release["releaseId"]:
            raise XingceSelectionIntentError(f"{key} release does not match intent")
    _validate_cause_focus(value["confirmedCauseFocus"])
    difficulty = value["difficultyPolicy"]
    _validate_difficulty_policy(difficulty)
    novelty = value["noveltyPolicy"]
    if novelty != {
        "answered": "exclude",
        "exposed": "exclude",
        "scope": "current_user",
    }:
        raise XingceSelectionIntentError("novelty policy cannot relax user isolation")
    refs = value["evidenceRefs"]
    if (
        not isinstance(refs, list)
        or not refs
        or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
        or len(set(refs)) != len(refs)
    ):
        raise XingceSelectionIntentError("selection evidence refs are malformed")
    if value["fallbackReason"] not in _FALLBACK_REASONS:
        raise XingceSelectionIntentError("selection fallback reason is unsupported")
    frozen = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    if frozen["intentId"] != _selection_intent_identity(frozen):
        raise XingceSelectionIntentError("selection intent id does not match payload")
    return frozen


def selection_intent_fingerprint(value: object) -> str:
    return str(validate_selection_intent(value)["intentId"])


def _selection_intent_identity(value: dict[str, Any]) -> str:
    identity = {key: item for key, item in value.items() if key != "intentId"}
    return sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_selection_node(
    value: object,
    *,
    key: str,
    expected_type: str,
) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != _NODE_FIELDS:
        raise XingceSelectionIntentError(f"{key} is malformed")
    if value["nodeType"] != expected_type:
        raise XingceSelectionIntentError(f"{key} has the wrong node type")
    for field in _NODE_FIELDS:
        if not isinstance(value[field], str) or not value[field].strip():
            raise XingceSelectionIntentError(f"{key}.{field} is malformed")


def _validate_cause_focus(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != _CAUSE_FIELDS:
        raise XingceSelectionIntentError("confirmed cause focus is malformed")
    for field in _CAUSE_FIELDS - {"questionType"}:
        if not isinstance(value[field], str) or not value[field].strip():
            raise XingceSelectionIntentError(f"confirmed cause focus {field} is malformed")
    if value["questionType"] is not None and (
        not isinstance(value["questionType"], str) or not value["questionType"].strip()
    ):
        raise XingceSelectionIntentError("confirmed cause question type is malformed")


def _validate_difficulty_policy(value: object) -> None:
    if not isinstance(value, dict):
        raise XingceSelectionIntentError("difficulty policy is malformed")
    state = value.get("state")
    if state == "known":
        if set(value) != {
            "state",
            "targetTheta",
            "sourceVersionId",
            "algorithmVersion",
        }:
            raise XingceSelectionIntentError("known difficulty policy fields changed")
        theta = value["targetTheta"]
        if isinstance(theta, bool) or not isinstance(theta, (int, float)) or not isfinite(theta):
            raise XingceSelectionIntentError("known difficulty target theta is malformed")
    elif state == "unknown":
        if set(value) != {
            "state",
            "reason",
            "sourceVersionId",
            "algorithmVersion",
        } or value["reason"] not in _UNKNOWN_DIFFICULTY_REASONS:
            raise XingceSelectionIntentError("unknown difficulty policy fields changed")
        if value["sourceVersionId"] is not None and (
            not isinstance(value["sourceVersionId"], str)
            or not value["sourceVersionId"].strip()
        ):
            raise XingceSelectionIntentError("unknown difficulty source is malformed")
    else:
        raise XingceSelectionIntentError("difficulty policy state is unsupported")
    if value["algorithmVersion"] != "ability-estimate-v2-shadow-1":
        raise XingceSelectionIntentError("difficulty algorithm version is unsupported")
    if state == "known" and (
        not isinstance(value["sourceVersionId"], str)
        or not value["sourceVersionId"].strip()
    ):
        raise XingceSelectionIntentError("known difficulty source is required")


def xingce_question_count(*, mode: str, estimated_minutes: int) -> int:
    """Derive the frozen bundle size from the immutable plan task."""
    if mode == "verification":
        return XINGCE_QUICK_QUESTION_BUNDLE_SIZE
    return min(
        XINGCE_MAX_QUESTION_BUNDLE_SIZE,
        max(
            XINGCE_QUICK_QUESTION_BUNDLE_SIZE,
            estimated_minutes // XINGCE_MINUTES_PER_QUESTION,
        ),
    )


def xingce_practice_minutes(*, available_minutes: int, question_supply: int) -> int:
    """Use up to half the day for one focused block while preserving essay."""
    minimum_minutes = XINGCE_QUICK_QUESTION_BUNDLE_SIZE * XINGCE_MINUTES_PER_QUESTION
    if (
        available_minutes < minimum_minutes
        or question_supply < XINGCE_QUICK_QUESTION_BUNDLE_SIZE
    ):
        return 0
    desired_minutes = min(60, available_minutes, max(20, available_minutes // 2))
    question_count = min(
        XINGCE_MAX_QUESTION_BUNDLE_SIZE,
        question_supply,
        max(
            XINGCE_QUICK_QUESTION_BUNDLE_SIZE,
            desired_minutes // XINGCE_MINUTES_PER_QUESTION,
        ),
    )
    return question_count * XINGCE_MINUTES_PER_QUESTION


def is_current_xingce_session(task_session: object) -> bool:
    """Classify missing markers as historical and reject every unknown marker."""
    try:
        scoring_bundle = task_session["scoring_bundle"]  # type: ignore[index]
    except (KeyError, TypeError):
        return False
    if not isinstance(scoring_bundle, dict):
        raise XingceSessionGenerationError("Xingce scoring bundle is malformed")
    if XINGCE_SESSION_GENERATION_KEY not in scoring_bundle:
        return False
    marker = scoring_bundle[XINGCE_SESSION_GENERATION_KEY]
    if marker == XINGCE_SESSION_GENERATION_V1:
        return True
    raise XingceSessionGenerationError(
        f"Unsupported Xingce session generation: {marker!r}"
    )
