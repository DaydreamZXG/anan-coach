from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayMaterialBundleVersion,
    EssayRewriteVersion,
    EssayReviewResponse,
    EssayRubricVersion,
    EssayScoreRange,
    EssayTaskVersion,
    EssayTransferVerificationVersion,
)


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
TASK_ID = UUID("00000000-0000-0000-0000-000000001001")
MATERIAL_ID = UUID("00000000-0000-0000-0000-000000001002")
RUBRIC_ID = UUID("00000000-0000-0000-0000-000000001003")
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000001004")
ANSWER_ID = UUID("00000000-0000-0000-0000-000000001005")
REVIEW_ID = UUID("00000000-0000-0000-0000-000000001006")


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _answer_ref(answer_id: UUID = ANSWER_ID) -> dict[str, object]:
    return {
        "answerVersionId": answer_id,
        "segmentId": "answer-1",
        "quote": "概括问题",
        "quoteSha256": _hash("概括问题"),
    }


def _material_ref(material_id: UUID = MATERIAL_ID) -> dict[str, object]:
    return {
        "materialBundleVersionId": material_id,
        "materialId": "material-1",
        "segmentId": "segment-1",
        "quote": "基层存在问题",
        "quoteSha256": _hash("基层存在问题"),
    }


def _audit(
    *, input_fingerprint: str | None = None, output_fingerprint: str | None = None
) -> dict[str, object]:
    return {
        "contractVersion": "essay-review-v2",
        "providerSchemaVersion": "provider-v1",
        "rubricVersion": "rubric-v1",
        "rubricSource": "official",
        "rubricVerification": "verified",
        "promptVersion": "prompt-v1",
        "provider": "provider",
        "model": "model",
        "inputFingerprint": input_fingerprint or "a" * 64,
        "outputFingerprint": output_fingerprint or "b" * 64,
        "generatedAt": NOW,
        "validUntil": NOW + timedelta(days=30),
    }


def _completed_review() -> dict[str, object]:
    answer_ref = _answer_ref()
    material_ref = _material_ref()
    return {
        "reviewVersionId": REVIEW_ID,
        "attemptId": ATTEMPT_ID,
        "taskVersionId": TASK_ID,
        "materialBundleVersionId": MATERIAL_ID,
        "answerVersionId": ANSWER_ID,
        "rubricVersionId": RUBRIC_ID,
        "status": "completed",
        "reviewScope": "grounded_full",
        "thinkingEvidenceAvailable": False,
        "readiness": {
            "materialComplete": True,
            "promptComplete": True,
            "answerComplete": True,
            "ocrConfirmed": True,
            "blockingCodes": [],
        },
        "score": {
            "maximum": 20.0,
            "scoreRange": {"lower": 10.0, "recommended": 12.0, "upper": 14.0},
            "confidence": "medium",
            "confidenceReasons": ["official rubric"],
        },
        "dimensions": [
            {
                "criterionCode": "content",
                "label": "内容",
                "maximum": 20.0,
                "scoreRange": {
                    "lower": 10.0,
                    "recommended": 12.0,
                    "upper": 14.0,
                },
                "rationale": "要点部分覆盖",
                "findingIds": ["finding-1"],
                "answerEvidenceRefs": [answer_ref],
                "materialEvidenceRefs": [material_ref],
            }
        ],
        "pointCoverage": [
            {
                "rubricPointId": "point-1",
                "state": "partial",
                "awardedRange": {
                    "lower": 5.0,
                    "recommended": 6.0,
                    "upper": 7.0,
                },
                "explanation": "提到问题但概括不完整",
                "answerEvidenceRefs": [answer_ref],
                "materialEvidenceRefs": [material_ref],
            }
        ],
        "claimEvidence": [],
        "findings": [
            {
                "findingId": "finding-1",
                "type": "abstraction",
                "severity": "medium",
                "diagnosis": "概括层级不足",
                "action": "合并同类事实",
                "attribution": "unknown",
                "answerEvidenceRefs": [answer_ref],
                "materialEvidenceRefs": [material_ref],
            }
        ],
        "rewritePlan": {
            "priorityFindingIds": ["finding-1"],
            "targetPointIds": ["point-1"],
            "constraints": [{"code": "word_limit", "value": "200"}],
            "steps": [
                {
                    "stepId": "step-1",
                    "instruction": "合并同类问题",
                    "successCriterion": "形成两个上位概念",
                }
            ],
        },
        "audit": _audit(),
        "supersedesReviewVersionId": None,
    }


def test_score_ranges_are_strict_finite_and_ordered() -> None:
    assert EssayScoreRange(lower=1.0, recommended=2.0, upper=3.0).upper == 3.0

    with pytest.raises(ValidationError):
        EssayScoreRange.model_validate(
            {"lower": "1", "recommended": 2.0, "upper": 3.0}
        )
    with pytest.raises(ValidationError, match="ordered"):
        EssayScoreRange(lower=3.0, recommended=2.0, upper=1.0)
    with pytest.raises(ValidationError):
        EssayScoreRange(lower=1.0, recommended=2.0, upper=float("inf"))


def test_versioned_source_content_requires_exact_hash_timezone_and_no_self_chain() -> None:
    task = {
        "taskVersionId": TASK_ID,
        "taskKey": "task-key",
        "versionNo": 1,
        "questionId": None,
        "questionType": "summary",
        "prompt": "请概括问题",
        "promptSha256": _hash("请概括问题"),
        "wordLimit": 200,
        "maximumScore": 20.0,
        "state": "published",
        "supersedesTaskVersionId": None,
        "createdAt": NOW,
    }
    assert EssayTaskVersion.model_validate(task).task_version_id == TASK_ID

    invalid_hash = {**task, "promptSha256": "f" * 64}
    with pytest.raises(ValidationError, match="promptSha256"):
        EssayTaskVersion.model_validate(invalid_hash)
    with pytest.raises(ValidationError):
        EssayTaskVersion.model_validate({**task, "createdAt": NOW.replace(tzinfo=None)})
    with pytest.raises(ValidationError, match="cannot supersede itself"):
        EssayTaskVersion.model_validate(
            {**task, "supersedesTaskVersionId": TASK_ID}
        )


def test_material_and_rubric_contracts_enforce_completeness_and_totals() -> None:
    bundle = {
        "materialBundleVersionId": MATERIAL_ID,
        "taskVersionId": TASK_ID,
        "versionNo": 1,
        "completeness": "complete",
        "sourceKind": "official",
        "contentSha256": "c" * 64,
        "segments": [
            {
                "materialId": "material-1",
                "segmentId": "segment-1",
                "materialOrder": 1,
                "segmentOrder": 1,
                "text": "基层存在问题",
                "textSha256": _hash("基层存在问题"),
            }
        ],
        "createdAt": NOW,
    }
    assert EssayMaterialBundleVersion.model_validate(bundle).segments
    with pytest.raises(ValidationError, match="requires segments"):
        EssayMaterialBundleVersion.model_validate({**bundle, "segments": []})

    rubric = {
        "rubricVersionId": RUBRIC_ID,
        "taskVersionId": TASK_ID,
        "materialBundleVersionId": MATERIAL_ID,
        "versionNo": 1,
        "sourceKind": "system_compiled",
        "verificationStatus": "unverified",
        "state": "published",
        "totalMaximum": 20.0,
        "criteria": [
            {
                "criterionCode": "content",
                "label": "内容",
                "maximum": 20.0,
                "scoringRule": "按要点评分",
                "materialEvidenceRequired": True,
            }
        ],
        "points": [
            {
                "rubricPointId": "point-1",
                "criterionCode": "content",
                "category": "problem",
                "label": "问题",
                "maximumCredit": 20.0,
                "materialSegmentIds": ["segment-1"],
                "materialEvidenceRefs": [
                    {
                        "materialBundleVersionId": MATERIAL_ID,
                        "materialId": "material-1",
                        "segmentId": "segment-1",
                        "startOffset": 0,
                        "endOffset": 4,
                        "quote": "材料原文",
                        "quoteSha256": _hash("材料原文"),
                    }
                ],
            }
        ],
        "contentSha256": "d" * 64,
        "createdAt": NOW,
    }
    assert EssayRubricVersion.model_validate(rubric).verification_status == "unverified"
    with pytest.raises(ValidationError, match="remain unverified"):
        EssayRubricVersion.model_validate({**rubric, "verificationStatus": "verified"})
    with pytest.raises(ValidationError, match="must sum"):
        EssayRubricVersion.model_validate(
            {**rubric, "totalMaximum": 25.0}
        )


def test_completed_review_is_grounded_and_cross_references_known_versions() -> None:
    payload = _completed_review()
    response = EssayReviewResponse.model_validate(payload)
    assert response.status == "completed"

    wrong_answer = deepcopy(payload)
    wrong_answer["dimensions"][0]["answerEvidenceRefs"][0][  # type: ignore[index]
        "answerVersionId"
    ] = UUID("00000000-0000-0000-0000-000000009999")
    with pytest.raises(ValidationError, match="reviewed answer version"):
        EssayReviewResponse.model_validate(wrong_answer)

    wrong_material = deepcopy(payload)
    wrong_material["findings"][0]["materialEvidenceRefs"][0][  # type: ignore[index]
        "materialBundleVersionId"
    ] = UUID("00000000-0000-0000-0000-000000009998")
    with pytest.raises(ValidationError, match="reviewed material bundle"):
        EssayReviewResponse.model_validate(wrong_material)

    unknown_rewrite_target = deepcopy(payload)
    unknown_rewrite_target["rewritePlan"]["priorityFindingIds"] = [  # type: ignore[index]
        "unknown"
    ]
    with pytest.raises(ValidationError, match="unknown finding"):
        EssayReviewResponse.model_validate(unknown_rewrite_target)

    duplicate_citation = deepcopy(payload)
    refs = duplicate_citation["dimensions"][0]["answerEvidenceRefs"]  # type: ignore[index]
    refs.append(deepcopy(refs[0]))
    with pytest.raises(ValidationError, match="references must be unique"):
        EssayReviewResponse.model_validate(duplicate_citation)


def test_completed_review_respects_readiness_thinking_and_rubric_confidence() -> None:
    not_ready = _completed_review()
    not_ready["readiness"]["materialComplete"] = False  # type: ignore[index]
    with pytest.raises(ValidationError, match="complete inputs"):
        EssayReviewResponse.model_validate(not_ready)

    invented_attribution = _completed_review()
    invented_attribution["findings"][0]["attribution"] = "found_not_written"  # type: ignore[index]
    with pytest.raises(ValidationError, match="thinking evidence"):
        EssayReviewResponse.model_validate(invented_attribution)

    overconfident = _completed_review()
    overconfident["audit"]["rubricSource"] = "system_compiled"  # type: ignore[index]
    overconfident["audit"]["rubricVerification"] = "unverified"  # type: ignore[index]
    with pytest.raises(ValidationError, match="caps confidence"):
        EssayReviewResponse.model_validate(overconfident)


def _rewrite_payload() -> dict[str, object]:
    return {
        "rewriteVersionId": UUID("00000000-0000-0000-0000-000000002001"),
        "attemptId": ATTEMPT_ID,
        "taskVersionId": TASK_ID,
        "baseReviewVersionId": REVIEW_ID,
        "baseAnswerVersionId": ANSWER_ID,
        "rewrittenAnswerVersionId": UUID(
            "00000000-0000-0000-0000-000000002002"
        ),
        "addressedFindingIds": ["finding-1"],
        "unresolvedFindingIds": ["finding-2"],
        "regressions": [],
        "pointCoverageDelta": [
            {"rubricPointId": "point-1", "fromState": "missing", "toState": "partial"}
        ],
        "dimensionRangeDelta": [
            {
                "criterionCode": "content",
                "before": {"lower": 8.0, "recommended": 10.0, "upper": 12.0},
                "after": {"lower": 10.0, "recommended": 12.0, "upper": 14.0},
            }
        ],
        "inputFingerprint": "a" * 64,
        "outputFingerprint": "b" * 64,
        "audit": _audit(),
    }


def test_rewrite_contract_requires_a_new_version_real_deltas_and_audit_binding() -> None:
    payload = _rewrite_payload()
    assert EssayRewriteVersion.model_validate(payload).addressed_finding_ids == [
        "finding-1"
    ]

    same_answer = {**payload, "rewrittenAnswerVersionId": ANSWER_ID}
    with pytest.raises(ValidationError, match="new answer version"):
        EssayRewriteVersion.model_validate(same_answer)

    overlap = {**payload, "unresolvedFindingIds": ["finding-1"]}
    with pytest.raises(ValidationError, match="both addressed and unresolved"):
        EssayRewriteVersion.model_validate(overlap)

    no_delta = deepcopy(payload)
    no_delta["pointCoverageDelta"][0]["toState"] = "missing"  # type: ignore[index]
    with pytest.raises(ValidationError, match="changed state"):
        EssayRewriteVersion.model_validate(no_delta)

    wrong_audit = deepcopy(payload)
    wrong_audit["audit"]["outputFingerprint"] = "c" * 64  # type: ignore[index]
    with pytest.raises(ValidationError, match="output fingerprint"):
        EssayRewriteVersion.model_validate(wrong_audit)


def _transfer_payload() -> dict[str, object]:
    transfer_answer = UUID("00000000-0000-0000-0000-000000003005")
    fresh_material = UUID("00000000-0000-0000-0000-000000003006")
    return {
        "verificationVersionId": UUID(
            "00000000-0000-0000-0000-000000003001"
        ),
        "userId": UUID("00000000-0000-0000-0000-000000003002"),
        "sourceReviewVersionId": REVIEW_ID,
        "sourceAttemptId": ATTEMPT_ID,
        "sourceTaskVersionId": TASK_ID,
        "transferAttemptId": UUID("00000000-0000-0000-0000-000000003003"),
        "transferTaskVersionId": UUID(
            "00000000-0000-0000-0000-000000003004"
        ),
        "transferAnswerVersionId": transfer_answer,
        "freshMaterialBundleVersionId": fresh_material,
        "status": "verified",
        "testedCriterionCodes": ["content"],
        "answerEvidenceRefs": [_answer_ref(transfer_answer)],
        "materialEvidenceRefs": [_material_ref(fresh_material)],
        "inputFingerprint": "a" * 64,
        "outputFingerprint": "b" * 64,
        "audit": _audit(),
    }


def test_transfer_verification_requires_fresh_task_and_bound_evidence() -> None:
    payload = _transfer_payload()
    assert EssayTransferVerificationVersion.model_validate(payload).status == "verified"

    same_task = {**payload, "transferTaskVersionId": TASK_ID}
    with pytest.raises(ValidationError, match="different task"):
        EssayTransferVerificationVersion.model_validate(same_task)

    no_evidence = {**payload, "answerEvidenceRefs": []}
    with pytest.raises(ValidationError, match="requires answer and material evidence"):
        EssayTransferVerificationVersion.model_validate(no_evidence)

    wrong_answer_ref = deepcopy(payload)
    wrong_answer_ref["answerEvidenceRefs"][0]["answerVersionId"] = ANSWER_ID  # type: ignore[index]
    with pytest.raises(ValidationError, match="wrong answer version"):
        EssayTransferVerificationVersion.model_validate(wrong_answer_ref)


def _seed_database_contract(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into learner.users (user_id) values
                  ('00000000-0000-0000-0000-000000004001'),
                  ('00000000-0000-0000-0000-000000004002');
                insert into product.essay_task_versions (
                  task_version_id, task_key, version_no, question_type, prompt,
                  prompt_sha256, word_limit, maximum_score, state
                ) values (
                  '00000000-0000-0000-0000-000000004101', 'db-task', 1,
                  'summary', 'prompt', repeat('a', 64), 200, 20, 'published'
                );
                insert into product.essay_material_bundle_versions (
                  material_bundle_version_id, task_version_id, task_key,
                  version_no, completeness, source_kind, content_sha256
                ) values (
                  '00000000-0000-0000-0000-000000004102',
                  '00000000-0000-0000-0000-000000004101', 'db-task', 1,
                  'complete', 'official', repeat('b', 64)
                );
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  material_bundle_version_id,
                  source_kind, verification_status, state, total_maximum,
                  criteria, points, content_sha256
                ) values (
                  '00000000-0000-0000-0000-000000004103',
                  '00000000-0000-0000-0000-000000004101', 'db-task', 1,
                  '00000000-0000-0000-0000-000000004102',
                  'official', 'verified', 'published', 20,
                  '[{}]'::jsonb, '[{}]'::jsonb, repeat('c', 64)
                );
                insert into learning.essay_attempts (
                  attempt_id, command_id, user_id, task_version_id, task_key,
                  material_bundle_version_id, rubric_version_id, source_context,
                  timed_status, input_fingerprint, submitted_at
                ) values
                  (
                    '00000000-0000-0000-0000-000000004201', 'attempt-1',
                    '00000000-0000-0000-0000-000000004001',
                    '00000000-0000-0000-0000-000000004101', 'db-task',
                    '00000000-0000-0000-0000-000000004102',
                    '00000000-0000-0000-0000-000000004103', 'in_program',
                    'untimed', repeat('d', 64), now()
                  ),
                  (
                    '00000000-0000-0000-0000-000000004202', 'attempt-2',
                    '00000000-0000-0000-0000-000000004002',
                    '00000000-0000-0000-0000-000000004101', 'db-task',
                    '00000000-0000-0000-0000-000000004102',
                    '00000000-0000-0000-0000-000000004103', 'paper',
                    'unknown', repeat('e', 64), now()
                  );
                insert into learning.essay_answer_versions (
                  answer_version_id, command_id, attempt_id, user_id,
                  task_version_id, version_no, source_type, answer_text,
                  answer_sha256, input_fingerprint
                ) values (
                  '00000000-0000-0000-0000-000000004301', 'answer-1',
                  '00000000-0000-0000-0000-000000004201',
                  '00000000-0000-0000-0000-000000004001',
                  '00000000-0000-0000-0000-000000004101', 1, 'typed',
                  'answer', repeat('f', 64), repeat('1', 64)
                )
                """
            )
        )


@pytest.mark.parametrize(
    ("score_confidence_reasons", "grounding_contract_version", "grounding_report"),
    [
        (
            None,
            "essay-grounding-v2",
            '{"verified": true, "eligibleForLearning": true}',
        ),
        (
            '["grounded"]',
            None,
            '{"verified": true, "eligibleForLearning": true}',
        ),
        ('["grounded"]', "essay-grounding-v2", None),
    ],
)
def test_database_completed_review_rejects_null_grounding_contract_fields(
    db_engine: Engine,
    score_confidence_reasons: str | None,
    grounding_contract_version: str | None,
    grounding_report: str | None,
) -> None:
    _seed_database_contract(db_engine)

    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into learning.essay_review_versions (
                      review_version_id, command_id, attempt_id, user_id,
                      task_version_id, answer_version_id,
                      material_bundle_version_id, rubric_version_id, status,
                      review_scope, thinking_evidence_available, readiness,
                      score_lower, score_recommended, score_upper, score_maximum,
                      score_confidence, score_confidence_reasons,
                      grounding_contract_version, grounding_report,
                      dimensions, point_coverage, claim_evidence, findings,
                      rewrite_plan, contract_version, provider_schema_version,
                      rubric_contract_version, prompt_version, provider, model,
                      input_fingerprint, execution_fingerprint,
                      execution_attempt_no, output_fingerprint,
                      generated_at, valid_until
                    ) values (
                      '00000000-0000-0000-0000-000000004403',
                      'review-null-grounding-contract',
                      '00000000-0000-0000-0000-000000004201',
                      '00000000-0000-0000-0000-000000004001',
                      '00000000-0000-0000-0000-000000004101',
                      '00000000-0000-0000-0000-000000004301',
                      '00000000-0000-0000-0000-000000004102',
                      '00000000-0000-0000-0000-000000004103',
                      'completed', 'grounded_full', false, '{}'::jsonb,
                      8, 10, 12, 20, 'medium',
                      cast(:score_confidence_reasons as jsonb),
                      :grounding_contract_version,
                      cast(:grounding_report as jsonb),
                      '[{}]'::jsonb, '[{}]'::jsonb, '[]'::jsonb,
                      '[{}]'::jsonb, '{}'::jsonb, 'essay-review-v2',
                      'provider-schema-v1', 'rubric-v1', 'prompt-v1',
                      'provider', 'model', repeat('4', 64), repeat('8', 64),
                      1, repeat('5', 64), now(), now() + interval '1 day'
                    )
                    """
                ),
                {
                    "score_confidence_reasons": score_confidence_reasons,
                    "grounding_contract_version": grounding_contract_version,
                    "grounding_report": grounding_report,
                },
            )


@pytest.mark.parametrize(
    ("compilation_input_fingerprint", "compilation_audit"),
    [
        (
            None,
            """{
              "contractVersion": "v1",
              "compilerVersion": "v1",
              "promptVersion": "v1",
              "provider": "provider",
              "model": "model",
              "inputFingerprint": "input",
              "promptSha256": "prompt",
              "materialContentSha256": "material"
            }""",
        ),
        ("9" * 64, None),
    ],
)
def test_database_system_rubric_rejects_null_compilation_contract_fields(
    db_engine: Engine,
    compilation_input_fingerprint: str | None,
    compilation_audit: str | None,
) -> None:
    _seed_database_contract(db_engine)

    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into product.essay_rubric_versions (
                      rubric_version_id, task_version_id, task_key, version_no,
                      material_bundle_version_id, compilation_input_fingerprint,
                      source_kind, verification_status, state, total_maximum,
                      criteria, points, content_sha256, compilation_audit
                    ) values (
                      '00000000-0000-0000-0000-000000004104',
                      '00000000-0000-0000-0000-000000004101', 'db-task', 2,
                      '00000000-0000-0000-0000-000000004102',
                      :compilation_input_fingerprint, 'system_compiled',
                      'unverified', 'draft', 20, '[{}]'::jsonb, '[{}]'::jsonb,
                      repeat('9', 64), cast(:compilation_audit as jsonb)
                    )
                    """
                ),
                {
                    "compilation_input_fingerprint": (
                        compilation_input_fingerprint
                    ),
                    "compilation_audit": compilation_audit,
                },
            )


def test_database_composite_foreign_keys_reject_cross_owner_chains(
    db_engine: Engine,
) -> None:
    _seed_database_contract(db_engine)

    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into learning.essay_answer_versions (
                      answer_version_id, command_id, attempt_id, user_id,
                      task_version_id, version_no, source_type, answer_text,
                      answer_sha256, input_fingerprint,
                      supersedes_answer_version_id
                    ) values (
                      '00000000-0000-0000-0000-000000004302', 'cross-owner',
                      '00000000-0000-0000-0000-000000004202',
                      '00000000-0000-0000-0000-000000004002',
                      '00000000-0000-0000-0000-000000004101', 1, 'typed',
                      'answer', repeat('a', 64), repeat('2', 64),
                      '00000000-0000-0000-0000-000000004301'
                    )
                    """
                )
            )

    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into learning.essay_answer_versions (
                      answer_version_id, command_id, attempt_id, user_id,
                      task_version_id, version_no, source_type, answer_text,
                      answer_sha256, input_fingerprint,
                      supersedes_answer_version_id
                    ) values (
                      '00000000-0000-0000-0000-000000004303', 'self-chain',
                      '00000000-0000-0000-0000-000000004201',
                      '00000000-0000-0000-0000-000000004001',
                      '00000000-0000-0000-0000-000000004101', 2, 'typed',
                      'answer', repeat('a', 64), repeat('3', 64),
                      '00000000-0000-0000-0000-000000004303'
                    )
                    """
                )
            )


def test_database_review_range_is_complete_and_ordered(db_engine: Engine) -> None:
    _seed_database_contract(db_engine)

    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into learning.essay_review_versions (
                      review_version_id, command_id, attempt_id, user_id,
                      task_version_id, answer_version_id,
                      material_bundle_version_id, rubric_version_id, status,
                      review_scope, thinking_evidence_available, readiness,
                      score_lower, score_recommended, score_upper, score_maximum,
                      score_confidence, score_confidence_reasons,
                      grounding_contract_version, grounding_report,
                      dimensions, point_coverage,
                      claim_evidence, findings, rewrite_plan, contract_version,
                      provider_schema_version, rubric_contract_version,
                      prompt_version, provider, model, input_fingerprint,
                      execution_fingerprint, execution_attempt_no,
                      output_fingerprint, generated_at, valid_until
                    ) values (
                      '00000000-0000-0000-0000-000000004401', 'review-invalid',
                      '00000000-0000-0000-0000-000000004201',
                      '00000000-0000-0000-0000-000000004001',
                      '00000000-0000-0000-0000-000000004101',
                      '00000000-0000-0000-0000-000000004301',
                      '00000000-0000-0000-0000-000000004102',
                      '00000000-0000-0000-0000-000000004103', 'completed',
                      'grounded_full', false, '{}'::jsonb,
                      10, 9, 8, 20, 'medium', '["test"]'::jsonb,
                      'essay-grounding-v2', jsonb_build_object('verified', true),
                      '[{}]'::jsonb, '[{}]'::jsonb,
                      '[]'::jsonb, '[{}]'::jsonb, '{}'::jsonb,
                      'v2', 'v1', 'rubric-v1', 'prompt-v1', 'provider', 'model',
                      repeat('4', 64), repeat('8', 64), 1,
                      repeat('5', 64), now(), now() + interval '1 day'
                    )
                    """
                )
            )

    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into learning.essay_review_versions (
                      review_version_id, command_id, attempt_id, user_id,
                      task_version_id, answer_version_id,
                      material_bundle_version_id, rubric_version_id, status,
                      review_scope, thinking_evidence_available, readiness,
                      score_lower, score_recommended, score_upper, score_maximum,
                      score_confidence, score_confidence_reasons,
                      grounding_contract_version, grounding_report,
                      dimensions, point_coverage,
                      claim_evidence, findings, rewrite_plan, contract_version,
                      provider_schema_version, rubric_contract_version,
                      prompt_version, provider, model, input_fingerprint,
                      execution_fingerprint, execution_attempt_no,
                      output_fingerprint, generated_at, valid_until
                    ) values (
                      '00000000-0000-0000-0000-000000004402', 'review-partial-range',
                      '00000000-0000-0000-0000-000000004201',
                      '00000000-0000-0000-0000-000000004001',
                      '00000000-0000-0000-0000-000000004101',
                      '00000000-0000-0000-0000-000000004301',
                      '00000000-0000-0000-0000-000000004102',
                      '00000000-0000-0000-0000-000000004103', 'completed',
                      'grounded_full', false, '{}'::jsonb,
                      8, null, 12, 20, 'medium', '["test"]'::jsonb,
                      'essay-grounding-v2', jsonb_build_object('verified', true),
                      '[{}]'::jsonb, '[{}]'::jsonb,
                      '[]'::jsonb, '[{}]'::jsonb, '{}'::jsonb,
                      'v2', 'v1', 'rubric-v1', 'prompt-v1', 'provider', 'model',
                      repeat('6', 64), repeat('9', 64), 1,
                      repeat('7', 64), now(), now() + interval '1 day'
                    )
                    """
                )
            )


def test_answer_contract_binds_hash_and_thinking_pair() -> None:
    payload = {
        "answerVersionId": ANSWER_ID,
        "attemptId": ATTEMPT_ID,
        "userId": UUID("00000000-0000-0000-0000-000000005001"),
        "taskVersionId": TASK_ID,
        "versionNo": 1,
        "sourceType": "typed",
        "answerText": "我的答案",
        "answerSha256": _hash("我的答案"),
        "thinking": {"outlineText": "先问题后对策"},
        "thinkingSha256": "a" * 64,
        "inputFingerprint": "b" * 64,
        "createdAt": NOW,
    }
    assert EssayAnswerVersion.model_validate(payload).thinking is not None
    with pytest.raises(ValidationError, match="appear together"):
        EssayAnswerVersion.model_validate({**payload, "thinkingSha256": None})
    with pytest.raises(ValidationError, match="answerSha256"):
        EssayAnswerVersion.model_validate({**payload, "answerSha256": "c" * 64})
