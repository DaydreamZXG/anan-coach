from __future__ import annotations

from datetime import UTC, datetime

from app.domains.xingce_learning.cause_agent.tools import _scratch_reasoning_item


ANCHOR = {
    "blockId": "stem-block-1",
    "segmentIndex": 0,
    "segmentText": "增长率不超过百分之十",
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
