from __future__ import annotations

from copy import deepcopy

from app.domains.teaching_packages.publication import (
    PublicationReasonCode,
    RuntimeAsset,
    RuntimeQuestion,
    build_media_publication_plan,
    project_formula_candidate,
    project_non_graphic_candidate,
)
from scripts.publish_question_media_evidence import _non_graphic_scope_occurrences
from app.domains.teaching_packages.publication.projector import (
    CandidateProjectionError,
)
import pytest


SOURCE_SHA = "a" * 64
CONTENT_VERSION = "b" * 64


def non_graphic_candidate(*, status: str = "success") -> dict:
    return {
        "assetId": "qi_1234567890abcdef12345678",
        "schemaVersion": "non-graphic-question-visual-v1",
        "route": "reasoning_diagram",
        "status": status,
        "subtype": "几何示意图",
        "source": {
            "sha256": SOURCE_SHA,
            "questionIds": ["pq_one"],
            "roles": ["stem"],
        },
        "reasoningLayer": {
            "schema_version": "reasoning-diagram-semantic-v1",
            "entities": [
                {"id": "n1", "label": "A", "description": "左侧点"},
                {"id": "n2", "label": "B", "description": "右侧点"},
            ],
            "connections": [
                {"from": "n1", "to": "n2", "kind": "line", "directed": False}
            ],
        },
        "renderLayer": {"kind": "primitivePanels"},
        "verification": {"sourceHashVerified": True},
    }


def formula_candidate(*, status: str = "candidate_auto", expression: str = r"x^2+1") -> dict:
    return {
        "asset_id": "qi_abcdef1234567890abcdef12",
        "schema_version": "formula-asset-v1",
        "sha256": SOURCE_SHA,
        "priority": "question_critical",
        "provider": "texo_local",
        "provider_request_id": None,
        "confidence": 0.99,
        "status": status,
        "source_path": "/private/formula.png",
        "blocks": [
            {"type": "formula", "format": "latex", "expression": expression, "display": True}
        ],
        "verification": {
            "source_hash_matches": True,
            "braces_balanced": True,
            "latex_nonempty": True,
            "note": "quality_gates=passed",
        },
    }


def runtime_question() -> RuntimeQuestion:
    return RuntimeQuestion(
        question_id="q1",
        status="active",
        content_version_id=CONTENT_VERSION,
    )


def runtime_asset(part_kind: str) -> RuntimeAsset:
    return RuntimeAsset(
        asset_id=f"asset-{part_kind}",
        question_id="q1",
        object_key="questions/source.png",
        content_sha256=SOURCE_SHA,
        part_kind=part_kind,
    )


def media_plan(candidate: dict, *, part_kind: str):
    return build_media_publication_plan(
        batch_id="media-batch-v1",
        candidates=[candidate],
        projector=project_non_graphic_candidate,
        source_keys_for_candidate=lambda item: item["source"]["questionIds"],
        occurrence_question_ids={"pq_one": "q1"},
        runtime_questions={"q1": runtime_question()},
        runtime_assets={"q1": (runtime_asset(part_kind),)},
    )


def test_non_graphic_projection_is_answer_free_and_does_not_mutate_source() -> None:
    candidate = non_graphic_candidate()
    before = deepcopy(candidate)

    projection = project_non_graphic_candidate(candidate)

    assert projection.evidence_ref == "question-media:qi_1234567890abcdef12345678"
    assert projection.payload["evidenceKind"] == "reasoning_diagram"
    assert projection.payload["reviewState"] == "verified"
    assert projection.payload["facts"]
    assert candidate == before
    serialized = str(projection.payload).lower()
    assert "answer" not in serialized
    assert "explanation" not in serialized


def test_needs_review_non_graphic_candidate_is_quarantined_not_published() -> None:
    plan = media_plan(non_graphic_candidate(status="needs_review"), part_kind="stem")

    assert not plan.publishable_items
    assert plan.quarantined_items[0].quarantine_reasons == (
        PublicationReasonCode.SEMANTIC_CONFLICT,
    )


@pytest.mark.parametrize(
    ("part_kind", "audiences"),
    (("stem", ["student", "verifier"]), ("analysis", ["verifier"])),
)
def test_runtime_part_kind_is_the_audience_authority(part_kind: str, audiences: list[str]) -> None:
    plan = media_plan(non_graphic_candidate(), part_kind=part_kind)

    assert len(plan.publishable_items) == 1
    assert plan.publishable_items[0].payload["audiences"] == audiences


def test_unknown_runtime_part_kind_is_quarantined() -> None:
    plan = media_plan(non_graphic_candidate(), part_kind="legacy_unknown")

    assert not plan.publishable_items
    assert plan.quarantined_items[0].quarantine_reasons == (
        PublicationReasonCode.SOURCE_AUDIENCE_UNRESOLVED,
    )


def test_formula_projection_preserves_machine_vs_override_review_identity() -> None:
    auto = project_formula_candidate(
        formula_candidate(),
        source_occurrences=({"question_id": "pq_one", "role": "stem"},),
    )
    override = project_formula_candidate(
        formula_candidate(status="verified_override"),
        source_occurrences=({"question_id": "pq_one", "role": "analysis"},),
    )

    assert auto.payload["reviewState"] == "machine_verified"
    assert auto.payload["facts"][0]["kind"] == "formula"
    assert auto.payload["facts"][0]["value"] == r"x^2+1"
    assert override.payload["reviewState"] == "verified"
    assert override.authority == "verified_override"


def test_unresolved_formula_placeholder_fails_closed() -> None:
    with pytest.raises(CandidateProjectionError, match="unresolved"):
        project_formula_candidate(
            formula_candidate(expression="[待复核]"),
            source_occurrences=({"question_id": "pq_one", "role": "stem"},),
        )


def test_formula_occurrences_exclude_graphic_reasoning_without_dropping_shared_assets() -> None:
    scoped = _non_graphic_scope_occurrences(
        [
            {
                "question_id": "pq_graphic",
                "role": "stem",
                "track": "graphic_reasoning",
            },
            {"question_id": "pq_quantity", "role": "stem", "track": "quantity"},
        ]
    )

    assert scoped == (
        {"question_id": "pq_quantity", "role": "stem", "track": "quantity"},
    )
