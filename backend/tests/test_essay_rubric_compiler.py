from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import UUID

import pytest

from app.domains.essay_intelligence.rubric_compiler import (
    EssayRubricCompilationError,
    RubricCompilationSource,
    RubricSourceSegment,
    SystemRubricCompilationDraft,
    compile_system_rubric,
    rubric_input_fingerprint,
)


TASK_ID = UUID("00000000-0000-0000-0000-000000000101")
MATERIAL_ID = UUID("00000000-0000-0000-0000-000000000102")
PROMPT = "请概括材料反映的问题，不超过200字。（20分）"
SEGMENT_TEXT = "基层事项多头填报，数据平台彼此隔离。"


def test_compiler_pins_unverified_system_rubric_to_exact_material_spans() -> None:
    source = _source()
    draft = _draft(source)

    compiled = compile_system_rubric(source=source, draft=draft)

    assert compiled.source_kind == "system_compiled"
    assert compiled.verification_status == "unverified"
    assert compiled.input_fingerprint == draft.input_fingerprint
    assert len(compiled.content_sha256) == 64
    assert compiled.points[0].criterion_code == "content"
    assert compiled.points[0].material_evidence_refs[0].quote == "多头填报"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("stale_input", "input_fingerprint_mismatch"),
        ("fake_quote", "evidence_quote_mismatch"),
        ("unknown_criterion", "point_criterion_unknown"),
        ("unconserved_credit", "point_credit_not_conserved"),
        ("duplicate_label", "duplicate_point_label"),
    ],
)
def test_compiler_rejects_unverifiable_or_non_conserving_drafts(
    mutation: str,
    expected_code: str,
) -> None:
    source = _source()
    raw = _draft_payload(source)
    if mutation == "stale_input":
        raw["inputFingerprint"] = "f" * 64
    elif mutation == "fake_quote":
        evidence = raw["points"][0]["materialEvidenceRefs"][0]
        evidence["quote"] = "虚假内容"
        evidence["endOffset"] = evidence["startOffset"] + len(evidence["quote"])
        evidence["quoteSha256"] = _hash(evidence["quote"])
    elif mutation == "unknown_criterion":
        raw["points"][0]["criterionCode"] = "unknown"
    elif mutation == "unconserved_credit":
        raw["points"][0]["maximumCredit"] = 14.0
    elif mutation == "duplicate_label":
        duplicate = deepcopy(raw["points"][0])
        duplicate["rubricPointId"] = "point-2"
        duplicate["label"] = " 多 头 填 报 "
        duplicate["materialEvidenceRefs"][0]["startOffset"] = 0
        duplicate["materialEvidenceRefs"][0]["endOffset"] = 2
        duplicate["materialEvidenceRefs"][0]["quote"] = "基层"
        duplicate["materialEvidenceRefs"][0]["quoteSha256"] = _hash("基层")
        duplicate["maximumCredit"] = 1.0
        raw["points"][0]["maximumCredit"] = 14.0
        raw["points"].append(duplicate)

    draft = SystemRubricCompilationDraft.model_validate(raw)
    with pytest.raises(EssayRubricCompilationError) as error:
        compile_system_rubric(source=source, draft=draft)

    assert expected_code in {item.code for item in error.value.issues}


def test_compiler_fingerprint_changes_with_any_full_source_segment() -> None:
    source = _source()
    first = rubric_input_fingerprint(
        source=source,
        compiler_version="compiler-v1",
        prompt_version="prompt-v1",
        provider="provider",
        model="model",
    )
    changed_text = SEGMENT_TEXT + "新增完整材料。"
    changed = RubricCompilationSource(
        **{
            **source.__dict__,
            "segments": (
                RubricSourceSegment(
                    material_id="material-1",
                    segment_id="segment-1",
                    text=changed_text,
                    text_sha256=_hash(changed_text),
                ),
            ),
        }
    )
    second = rubric_input_fingerprint(
        source=changed,
        compiler_version="compiler-v1",
        prompt_version="prompt-v1",
        provider="provider",
        model="model",
    )

    assert first != second


def _source() -> RubricCompilationSource:
    return RubricCompilationSource(
        task_version_id=TASK_ID,
        task_key="guokao-essay:question-1",
        question_type="summary",
        prompt=PROMPT,
        prompt_sha256=_hash(PROMPT),
        maximum_score=20.0,
        material_bundle_version_id=MATERIAL_ID,
        material_content_sha256="a" * 64,
        segments=(
            RubricSourceSegment(
                material_id="material-1",
                segment_id="segment-1",
                text=SEGMENT_TEXT,
                text_sha256=_hash(SEGMENT_TEXT),
            ),
        ),
    )


def _draft(source: RubricCompilationSource) -> SystemRubricCompilationDraft:
    return SystemRubricCompilationDraft.model_validate(_draft_payload(source))


def _draft_payload(source: RubricCompilationSource) -> dict[str, object]:
    compiler_version = "compiler-v1"
    prompt_version = "prompt-v1"
    provider = "provider"
    model = "model"
    return {
        "contractVersion": "guokao-essay-rubric-v1",
        "taskVersionId": source.task_version_id,
        "materialBundleVersionId": source.material_bundle_version_id,
        "promptSha256": source.prompt_sha256,
        "materialContentSha256": source.material_content_sha256,
        "compilerVersion": compiler_version,
        "promptVersion": prompt_version,
        "provider": provider,
        "model": model,
        "inputFingerprint": rubric_input_fingerprint(
            source=source,
            compiler_version=compiler_version,
            prompt_version=prompt_version,
            provider=provider,
            model=model,
        ),
        "sourceKind": "system_compiled",
        "verificationStatus": "unverified",
        "totalMaximum": 20.0,
        "criteria": [
            {
                "criterionCode": "content",
                "label": "内容要点",
                "maximum": 15.0,
                "scoringRule": "只按材料证据要点计分。",
                "materialEvidenceRequired": True,
            },
            {
                "criterionCode": "expression",
                "label": "表达规范",
                "maximum": 5.0,
                "scoringRule": "根据表达简洁和字数约束计分。",
                "materialEvidenceRequired": False,
            },
        ],
        "points": [
            {
                "rubricPointId": "point-1",
                "criterionCode": "content",
                "category": "problem",
                "label": "多头填报",
                "maximumCredit": 15.0,
                "materialSegmentIds": ["segment-1"],
                "materialEvidenceRefs": [
                    {
                        "materialBundleVersionId": source.material_bundle_version_id,
                        "materialId": "material-1",
                        "segmentId": "segment-1",
                        "startOffset": 4,
                        "endOffset": 8,
                        "quote": "多头填报",
                        "quoteSha256": _hash("多头填报"),
                    }
                ],
            }
        ],
    }


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
