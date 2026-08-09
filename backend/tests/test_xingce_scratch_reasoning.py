from __future__ import annotations

import base64
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import pytest

from app.domains.xingce_learning.cause_agent.tools import (
    CauseAgentTools,
    _scratch_reasoning_item,
)
from app.domains.xingce_learning.real_attempt.scratch_evidence import (
    build_scratch_evidence_projection,
)
from app.domains.math_ink.provider import (
    FORMULA_CANDIDATE_MEDIUM_THRESHOLD,
    FORMULA_CONFIDENCE_POLICY_VERSION,
    FORMULA_CONFIDENCE_SEMANTICS,
)


ANCHOR = {
    "blockId": "stem-block-1",
    "segmentIndex": 0,
    "segmentText": "增长率不超过百分之十",
}


def _recognition_metadata(*, confidence: float | None) -> dict[str, Any]:
    return {
        "contractVersion": "formula-recognition-metadata@v1",
        "provider": "Xfyun",
        "candidateSource": "xfyun_candidate",
        "providerRelease": "xfyun-formula-release-1",
        "modelRelease": "teach-photo-print",
        "priceVersion": "xfyun-price-1",
        "unitPriceCny": "0.01",
        "deploymentId": None,
        "rendererVersion": "math-ink-renderer@v1",
        "confidence": confidence,
        "providerRequestIdFingerprint": None,
        "vectorFingerprint": "a" * 64,
        "renderFingerprint": "b" * 64,
    }


def test_scratch_reasoning_consumes_text_formula_shape_sequence_and_revision() -> None:
    answer = {
        "draftText": "先设增长量为 x，再核对单位",
        "annotations": [
            _stroke(
                "highlight",
                1_000,
                [(0.1, 0.1), (0.45, 0.1)],
                tool="highlighter",
                anchor=ANCHOR,
            ),
            _stroke(
                "underline",
                2_000,
                [(0.1, 0.2), (0.3, 0.205), (0.5, 0.2)],
                anchor=ANCHOR,
            ),
            _stroke(
                "circle",
                3_000,
                [
                    (0.2, 0.3),
                    (0.3, 0.25),
                    (0.4, 0.3),
                    (0.4, 0.4),
                    (0.3, 0.45),
                    (0.2, 0.4),
                    (0.2, 0.3),
                ],
                anchor=ANCHOR,
            ),
            _stroke(
                "cross-a",
                4_000,
                [(0.2, 0.5), (0.5, 0.8)],
                anchor=ANCHOR,
            ),
            _stroke(
                "cross-b",
                4_500,
                [(0.2, 0.8), (0.5, 0.5)],
                anchor=ANCHOR,
            ),
            _stroke(
                "freehand",
                11_000,
                [(0.6, 0.4), (0.63, 0.55), (0.69, 0.42)],
                erased_at_ms=13_000,
            ),
        ],
    }
    formulas = [
        {
            "formula_version_id": "formula-v1",
            "latex": "x=(120-100)/100",
            "source": "user_confirmed",
            "confirmed_at": datetime(2026, 8, 8, 7, 0, tzinfo=UTC),
        }
    ]

    first = _scratch_reasoning_item(
        question_id="q-1",
        answer=answer,
        confirmed_formulas=formulas,
    )
    replay = _scratch_reasoning_item(
        question_id="q-1",
        answer=answer,
        confirmed_formulas=formulas,
    )

    assert first is not None
    assert replay is not None
    assert first["inputFingerprint"] == replay["inputFingerprint"]
    assert first["typedDraft"] == "先设增长量为 x，再核对单位"
    assert first["confirmedFormulas"][0]["latex"] == "x=(120-100)/100"
    kinds = [item["kind"] for item in first["observations"]]
    assert kinds == [
        "highlighted_text",
        "underlined_text",
        "circled_text",
        "crossed_out_text",
        "freehand_unknown",
    ]
    assert first["observations"][0]["anchor"]["segmentText"] == ANCHOR["segmentText"]
    assert first["observations"][3]["sourceStrokeIds"] == ["cross-a", "cross-b"]
    assert first["unknownFreehandCount"] == 1
    assert first["coverage"] == "partial"
    assert first["revisionCount"] == 1
    assert first["phases"] == [
        {
            "phaseNo": 1,
            "fromMs": 1_000,
            "toMs": 4_500,
            "observationRefs": [
                "scratch-observation-1",
                "scratch-observation-2",
                "scratch-observation-3",
                "scratch-observation-4",
            ],
            "containsRevision": False,
        },
        {
            "phaseNo": 2,
            "fromMs": 11_000,
            "toMs": 13_000,
            "observationRefs": ["scratch-observation-5"],
            "containsRevision": True,
        },
    ]
    assert set(first["reasoningSignals"]) == {
        "externalized_calculation_or_notes",
        "focused_on_anchored_text",
        "revised_written_work",
    }
    assert first["limitations"] == ["未锚定的自由笔迹未做文字猜测"]


def test_single_diagonal_mark_never_becomes_a_crossed_out_claim() -> None:
    item = _scratch_reasoning_item(
        question_id="q-2",
        answer={
            "annotations": [
                _stroke(
                    "single-diagonal",
                    2_000,
                    [(0.1, 0.1), (0.5, 0.5)],
                    anchor=ANCHOR,
                )
            ]
        },
        confirmed_formulas=[],
    )

    assert item is not None
    assert item["observations"][0]["kind"] == "marked_text"
    assert item["observations"][0]["confidence"] < 0.8
    assert item["coverage"] == "structured"


def test_empty_scratch_produces_no_reasoning_evidence() -> None:
    assert (
        _scratch_reasoning_item(
            question_id="q-empty",
            answer={"draftText": "", "annotations": []},
            confirmed_formulas=[],
        )
        is None
    )


def test_submit_projection_is_deterministic_conservative_and_png_bounded() -> None:
    created_at = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    answer = {
        "selectedOptionIds": ["A"],
        "eliminatedOptionIds": ["B"],
        "confidence": "medium",
        "marked": True,
        "skipped": False,
        "elapsedSeconds": 45,
        "materialPosition": 2,
        "draftText": "先列式，再核对单位",
        "annotations": [
            _stroke(
                "anchored-stroke",
                1_000,
                [(0.1, 0.1), (0.5, 0.1)],
                anchor=ANCHOR,
            ),
            _stroke(
                "freehand-stroke",
                2_000,
                [(0.2, 0.2), (0.3, 0.4), (0.4, 0.2)],
            ),
        ],
        "actionEvents": [
            {"type": "option_selected", "atMs": 500, "optionId": "A"},
            {"type": "draft_saved", "atMs": 2_100},
        ],
        "actionEventsTruncated": False,
    }
    formula_rows = [
        {
            "formula_version_id": "candidate-formula",
            "version_no": 1,
            "state": "candidate",
            "latex": "x=1",
            "source": "xfyun_candidate",
            "recognition_metadata": _recognition_metadata(confidence=0.99),
            "created_at": created_at,
            "confirmed_at": None,
        },
        {
            "formula_version_id": "confirmed-formula",
            "version_no": 2,
            "state": "confirmed",
            "latex": "x=2",
            "source": "user_confirmed",
            "recognition_metadata": None,
            "created_at": created_at,
            "confirmed_at": created_at,
        },
    ]
    kwargs = {
        "projection_id": "attempt-1",
        "session_id": "session-1",
        "question_id": "question-1",
        "content_version_id": "c" * 64,
        "answer_version_id": "answer-version-1",
        "answer_payload_sha256": "d" * 64,
        "answer": answer,
        "question": {"questionId": "question-1", "stem": "题干"},
        "material": {"materialId": "material-1", "text": "材料"},
        "result": {"resultCode": "incorrect"},
        "formula_versions": formula_rows,
        "created_at": created_at,
    }
    first, first_fingerprint = build_scratch_evidence_projection(**kwargs)
    replay, replay_fingerprint = build_scratch_evidence_projection(**kwargs)

    assert first == replay
    assert first_fingerprint == replay_fingerprint
    assert first["typedDraft"] == "先列式，再核对单位"
    assert first["actionSequence"] == answer["actionEvents"]
    assert first["recognition"]["state"] == "confirmed"
    assert "formula-version:confirmed-formula" in first["evidenceStrength"]["strong"]
    assert "formula-version:candidate-formula" in first["evidenceStrength"]["medium"]
    assert "annotation-anchor:anchored-stroke" in first["evidenceStrength"]["weak"]
    assert first["recognition"]["confidencePolicyVersion"] == (
        FORMULA_CONFIDENCE_POLICY_VERSION
    )
    assert first["recognition"]["candidateMediumThreshold"] == (
        FORMULA_CANDIDATE_MEDIUM_THRESHOLD
    )
    assert first["recognition"]["confidenceSemantics"] == (
        FORMULA_CONFIDENCE_SEMANTICS
    )
    candidate_source = next(
        item
        for item in first["recognition"]["sourceVersions"]
        if item["formulaVersionId"] == "candidate-formula"
    )
    assert candidate_source["confidence"] == 0.99
    assert candidate_source["strength"] == "medium"
    snapshots = first["annotations"]["renderSnapshots"]
    assert snapshots
    for snapshot in snapshots:
        encoded = snapshot["pngBase64"]
        assert len(base64.b64decode(encoded)) <= 128 * 1024


def test_oversized_annotation_never_embeds_an_unbounded_png() -> None:
    points = [{"x": (index % 100) / 100, "y": (index % 97) / 97} for index in range(20_001)]
    projection, _ = build_scratch_evidence_projection(
        projection_id="attempt-large",
        session_id="session-large",
        question_id="question-large",
        content_version_id="c" * 64,
        answer_version_id="answer-large",
        answer_payload_sha256="d" * 64,
        answer={
            "draftText": "",
            "annotations": [
                {
                    "strokeId": "oversized",
                    "surface": "question",
                    "tool": "pen",
                    "points": points,
                }
            ],
        },
        question={"questionId": "question-large"},
        material=None,
        result={"resultCode": "incorrect"},
        formula_versions=[],
        created_at=datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
    )
    assert projection["annotations"]["renderSnapshots"] == []
    assert "question_render_invalid" in projection["limitations"]


@pytest.mark.parametrize(
    ("metadata", "expected_strength"),
    [
        (_recognition_metadata(confidence=0.94), "weak"),
        (_recognition_metadata(confidence=None), "weak"),
        ({"confidence": 0.99}, "weak"),
    ],
)
def test_candidate_formula_needs_complete_high_confidence_receipt_for_medium(
    metadata: dict[str, Any],
    expected_strength: str,
) -> None:
    projection, _ = build_scratch_evidence_projection(
        projection_id="attempt-confidence",
        session_id="session-confidence",
        question_id="question-confidence",
        content_version_id="c" * 64,
        answer_version_id="answer-confidence",
        answer_payload_sha256="d" * 64,
        answer={},
        question={"questionId": "question-confidence"},
        material=None,
        result={"resultCode": "incorrect"},
        formula_versions=[
            {
                "formula_version_id": "candidate-confidence",
                "version_no": 1,
                "state": "candidate",
                "latex": "x=1",
                "source": "xfyun_candidate",
                "recognition_metadata": metadata,
                "created_at": datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
                "confirmed_at": None,
            }
        ],
        created_at=datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
    )
    assert projection["recognition"]["strength"] == expected_strength
    assert "formula-version:candidate-confidence" in projection[
        "evidenceStrength"
    ][expected_strength]


def test_cause_reads_only_exact_formula_successor_refs_and_rejects_tampering() -> None:
    confirmed = {
        "formula_version_id": "00000000-0000-0000-0000-000000000022",
        "question_id": "question-1",
        "state": "confirmed",
        "latex": "x=2",
        "source": "user_confirmed",
        "created_at": datetime(2026, 8, 9, 8, 1, tzinfo=UTC),
        "confirmed_at": datetime(2026, 8, 9, 8, 1, tzinfo=UTC),
        "recognition_metadata": _recognition_metadata(confidence=0.99),
    }
    repository = _ExactFormulaRepository([confirmed])
    exact_ref = (
        f"formula-version:{confirmed['formula_version_id']}:"
        f"{sha256(b'x=2').hexdigest()}"
    )
    tools = CauseAgentTools(
        session=object(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        user_id="00000000-0000-0000-0000-000000000001",
        session_id="00000000-0000-0000-0000-000000000002",
        reflection_version_id="00000000-0000-0000-0000-000000000003",
        input_version_refs=[exact_ref],
    )
    assert tools._exact_formula_rows() == [confirmed]
    assert repository.requested_ids == [confirmed["formula_version_id"]]
    scope = {
        "userId": "00000000-0000-0000-0000-000000000001",
        "sessionId": "00000000-0000-0000-0000-000000000002",
        "reflectionVersionId": "00000000-0000-0000-0000-000000000003",
    }
    item = tools.load_ink_formulas(scope)["items"][0]
    assert item["state"] == "confirmed"
    assert item["confidence"] == 0.99
    assert item["strength"] == "strong"
    assert item["sourceVersionRef"] == (
        f"formula-version:{confirmed['formula_version_id']}"
    )
    assert item["recognitionMetadata"]["vectorFingerprint"] == "a" * 64

    tampered = CauseAgentTools(
        session=object(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        user_id="00000000-0000-0000-0000-000000000001",
        session_id="00000000-0000-0000-0000-000000000002",
        reflection_version_id="00000000-0000-0000-0000-000000000003",
        input_version_refs=[
            f"formula-version:{confirmed['formula_version_id']}:{'0' * 64}"
        ],
    )
    with pytest.raises(PermissionError, match="fingerprint changed"):
        tampered._exact_formula_rows()


class _ExactFormulaRepository:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.requested_ids: list[str] = []

    def formula_versions_by_ids(self, _session: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.requested_ids = list(kwargs["formula_version_ids"])
        return [
            row for row in self.rows
            if str(row["formula_version_id"]) in self.requested_ids
        ]

    def attempt_evidence(self, _session: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{"question_id": "question-1", "result_code": "incorrect"}]


def test_historical_non_formula_refs_keep_dynamic_formula_and_legacy_alias() -> None:
    repository = _HistoricalFormulaRepository()
    tools = CauseAgentTools(
        session=object(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        user_id="00000000-0000-0000-0000-000000000001",
        session_id="00000000-0000-0000-0000-000000000002",
        reflection_version_id="00000000-0000-0000-0000-000000000003",
        input_version_refs=["reflection-version:legacy"],
    )
    result = tools.load_action_trace(
        {
            "userId": "00000000-0000-0000-0000-000000000001",
            "sessionId": "00000000-0000-0000-0000-000000000002",
            "reflectionVersionId": "00000000-0000-0000-0000-000000000003",
        }
    )
    scratch = result["scratchReasoning"]
    assert scratch["evidenceRefs"] == ["scratch-reasoning:question-1"]
    assert scratch["items"][0]["projectionRef"] == (
        "scratch-reasoning:question-1"
    )
    assert scratch["items"][0]["confirmedFormulas"][0]["latex"] == "x=2"


class _HistoricalFormulaRepository:
    def attempt_evidence(self, _session: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "question_id": "question-1",
                "result_code": "incorrect",
                "answer_payload": {"draftText": "x=2", "annotations": []},
                "scratch_evidence_projection": None,
                "scratch_evidence_fingerprint": None,
                "attempt_record_id": "attempt-1",
            }
        ]

    def session_bundle(self, _session: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"safe_bundle": {}, "scoring_bundle": {}}

    def formula_versions_by_ids(self, _session: Any, **_kwargs: Any) -> list[Any]:
        return []

    def ink_formula_evidence(self, _session: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "formula_version_id": "historical-formula",
                "question_id": "question-1",
                "state": "confirmed",
                "latex": "x=2",
                "source": "user_confirmed",
                "created_at": datetime(2026, 8, 8, 8, 0, tzinfo=UTC),
                "confirmed_at": datetime(2026, 8, 8, 8, 1, tzinfo=UTC),
                "recognition_metadata": None,
            }
        ]


def _stroke(
    stroke_id: str,
    at_ms: int,
    points: list[tuple[float, float]],
    *,
    tool: str = "pen",
    anchor: dict[str, object] | None = None,
    erased_at_ms: int | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "strokeId": stroke_id,
        "surface": "question",
        "tool": tool,
        "width": 0.02,
        "points": [{"x": x, "y": y} for x, y in points],
        "atMs": at_ms,
    }
    if anchor is not None:
        result["anchor"] = anchor
    if erased_at_ms is not None:
        result["erasedAtMs"] = erased_at_ms
    return result
