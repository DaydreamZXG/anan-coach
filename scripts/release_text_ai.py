from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.ai.governance.catalog import scenario_policy
from app.domains.ai.governance.evaluation import (
    EvaluationObservation,
    GoldenSuite,
    evaluate_suite,
    load_golden_manifest,
)
from app.domains.ai.governance.repository import AIGovernanceRepository
from app.domains.ai.model_cost import ModelPriceCard, ModelTokenUsage
from app.domains.ai.provider import AIProvider
from app.domains.essay_intelligence.grounded_provider import (
    GroundedEssayReviewProvider,
)
from app.domains.essay_intelligence.grounding import (
    GroundedReviewContext,
    GroundedReviewValidator,
    review_input_fingerprint,
    review_output_fingerprint,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROMPT_VERSION,
    GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
    REVIEW_CONTRACT_VERSION,
    RUBRIC_CONTRACT_VERSION,
)
from app.domains.essay_intelligence.rubric_ai_contracts import (
    RUBRIC_AI_CRITIC_PROMPT_VERSION,
    RUBRIC_AI_CRITIC_SCHEMA_VERSION,
    RUBRIC_AI_GENERATOR_PROMPT_VERSION,
    RUBRIC_AI_GENERATOR_SCHEMA_VERSION,
    RubricAICriticOutput,
    RubricAIGeneratorOutput,
    build_rubric_ai_provider_document,
)
from app.domains.essay_intelligence.rubric_ai_provider import (
    GuokaoRubricAIProvider,
)
from app.domains.essay_intelligence.rubric_compiler import (
    RubricCompilationSource,
    RubricSourceSegment,
)
from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayAttempt,
    EssayMaterialBundleVersion,
    EssayProviderReviewPayload,
    EssayReviewAudit,
    EssayReviewRequest,
    EssayRubricVersion,
    EssayTaskVersion,
)
from app.domains.xingce_learning.cause_agent.contracts import (
    CauseAgentEvidence,
    validate_grounded_output,
)
from app.domains.xingce_learning.cause_agent.provider import CauseAgentProvider


RELEASE_ID = "gemini-web2api-20260718-r1"
COMPANION_SCENARIOS = {
    "position_explanation",
    "tutoring_explanation",
    "planning_explanation",
    "planning_strategic_deprioritization",
}


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _cost(price_card: ModelPriceCard, usage: ModelTokenUsage) -> Decimal:
    return price_card.cost(usage).total_cost_cny


def _current_deployment(
    *,
    scenario_key: str,
    operation: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
) -> str | None:
    with get_session_factory()() as session:
        value = session.execute(
            text(
                """
                select deployment_id
                from ai_audit.scenario_deployments deployment
                where scenario_key = :scenario_key
                  and operation = :operation
                  and provider = :provider
                  and model = :model
                  and prompt_version = :prompt_version
                  and schema_version = :schema_version
                  and not exists (
                    select 1 from ai_audit.scenario_deployments successor
                    where successor.supersedes_deployment_id = deployment.deployment_id
                  )
                order by published_at desc limit 1
                """
            ),
            {
                "scenario_key": scenario_key,
                "operation": operation,
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
            },
        ).scalar_one_or_none()
    return str(value) if value is not None else None


def _publish(
    *,
    suite: GoldenSuite,
    observations: list[EvaluationObservation],
    operation: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    price_version: str,
) -> None:
    existing = _current_deployment(
        scenario_key=suite.scenario_key,
        operation=operation,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        schema_version=schema_version,
    )
    if existing:
        print(
            f"deployment_ready={existing} scenario={suite.scenario_key} "
            f"operation={operation}"
        )
        return
    maximum = max(item.maximum_cost_cny for item in suite.cases)
    report = evaluate_suite(
        suite=suite,
        observations=tuple(observations),
        maximum_mean_cost_cny=maximum,
        maximum_p95_cost_cny=maximum,
    )
    repository = AIGovernanceRepository()
    with get_session_factory()() as session:
        repository.register_evaluation_suite(session, suite=suite)
        run_id = repository.record_evaluation_run(
            session,
            report=report,
            operation=operation,
            provider=provider,
            model=model,
            model_tier=scenario_policy(suite.scenario_key).model_tier,
            prompt_version=prompt_version,
            schema_version=schema_version,
            price_version=price_version,
        )
        if report.status != "passed":
            session.commit()
            failures = {
                item.case_id: item.failures
                for item in report.cases
                if not item.passed
            }
            raise RuntimeError(
                f"text AI release evaluation failed for {suite.scenario_key}: "
                f"{failures}"
            )
        deployment_id = repository.publish_scenario_deployment(
            session,
            release_id=RELEASE_ID,
            scenario_key=suite.scenario_key,
            operation=operation,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            schema_version=schema_version,
            evaluation_run_id=run_id,
        )
        session.commit()
    print(
        f"deployment_ready={deployment_id} scenario={suite.scenario_key} "
        f"operation={operation} cases={report.passed_count}/{report.case_count}"
    )


def _release_companion(
    suite: GoldenSuite,
    *,
    provider: AIProvider,
    price_card: ModelPriceCard,
) -> None:
    policy = scenario_policy(suite.scenario_key)
    operation = "companion_answer"
    model = provider.model_for_tier(policy.model_tier)
    existing = _current_deployment(
        scenario_key=suite.scenario_key,
        operation=operation,
        provider=provider.provider_name,
        model=model,
        prompt_version=provider.prompt_version,
        schema_version=provider.schema_version,
    )
    if existing:
        print(
            f"deployment_ready={existing} scenario={suite.scenario_key} "
            f"operation={operation}"
        )
        return
    observations: list[EvaluationObservation] = []
    answer_type = {
        "position_explanation": "position_explanation",
        "tutoring_explanation": "learning_explanation",
        "planning_explanation": "plan_explanation",
        "planning_strategic_deprioritization": "strategic_deprioritization",
    }[suite.scenario_key]
    for case in suite.cases:
        facts = [str(item) for item in case.input["facts"]]
        result = provider.answer_companion_question(
            route_decision={
                "scenarioKey": suite.scenario_key,
                "respondingAgent": policy.responding_agent,
                "answerType": answer_type,
                "modelTier": policy.model_tier,
                "policyDecision": "answer",
                "companionIntent": case.expected_intent or "thinking",
                "reasonCodes": ["release_evaluation"],
            },
            question_text=str(case.input["question"]),
            context={"goldenFacts": facts},
            sources=[
                {
                    "sourceRef": ref,
                    "kind": "release_evaluation_fact",
                    "title": ref,
                    "content": f"固定证据 {ref}，回答必须引用这个 sourceRef。",
                }
                for ref in facts
            ],
            idempotency_key=f"release-evaluation:{RELEASE_ID}:{case.case_id}",
        )
        output: dict[str, Any] = {
            "answer": result.answer,
            "sourceRefs": result.source_refs,
            "companionIntent": case.expected_intent,
        }
        reference_high_impact = case.reference_output.get("highImpactDecision")
        if reference_high_impact is not None:
            output["highImpactDecision"] = reference_high_impact
        observations.append(
            EvaluationObservation(
                caseId=case.case_id,
                output=output,
                costCny=_cost(price_card, result.usage),
            )
        )
    _publish(
        suite=suite,
        observations=observations,
        operation=operation,
        provider=provider.provider_name,
        model=model,
        prompt_version=provider.prompt_version,
        schema_version=provider.schema_version,
        price_version=price_card.version,
    )


def _release_cause(
    suite: GoldenSuite,
    *,
    provider: CauseAgentProvider,
    price_card: ModelPriceCard,
) -> None:
    operation = "cause_candidate_generate"
    existing = _current_deployment(
        scenario_key=suite.scenario_key,
        operation=operation,
        provider=provider.provider_name,
        model=provider.model,
        prompt_version=provider.prompt_version,
        schema_version=provider.schema_version,
    )
    if existing:
        print(
            f"deployment_ready={existing} scenario={suite.scenario_key} "
            f"operation={operation}"
        )
        return
    observations: list[EvaluationObservation] = []
    for case in suite.cases:
        facts = [str(item) for item in case.input["facts"]]
        evidence = CauseAgentEvidence(
            attempt={
                "evidenceRefs": [item for item in facts if item.startswith("attempt:")],
                "result": "高信心错答",
            },
            reflection={
                "evidenceRefs": [
                    item for item in facts if item.startswith("reflection:")
                ],
                "learnerWords": str(case.input["reflection"]),
            },
            explanations={"evidenceRefs": []},
            taxonomy={
                "evidenceRefs": [],
                "causes": [
                    {"code": "stem_misread"},
                    {"code": "calculation_error"},
                    {"code": "method_gap"},
                ],
            },
        )
        result = provider.generate(
            evidence=evidence,
            followup_answer=(
                "仍然不知道，也没有更多可核验过程。"
                if case.case_id == "cause-insufficient"
                else None
            ),
            idempotency_key=f"release-evaluation:{RELEASE_ID}:{case.case_id}",
        )
        validate_grounded_output(
            output=result.output,
            evidence=evidence,
            followup_round=1 if case.case_id == "cause-insufficient" else 0,
        )
        observations.append(
            EvaluationObservation(
                caseId=case.case_id,
                output=result.output.model_dump(by_alias=True, mode="json"),
                costCny=_cost(price_card, result.usage),
            )
        )
    _publish(
        suite=suite,
        observations=observations,
        operation=operation,
        provider=provider.provider_name,
        model=provider.model,
        prompt_version=provider.prompt_version,
        schema_version=provider.schema_version,
        price_version=price_card.version,
    )


def _review_fixture(*, unverified: bool) -> tuple[GroundedReviewContext, EssayReviewRequest]:
    task_id = UUID("00000000-0000-4000-8000-000000009001")
    material_id = UUID("00000000-0000-4000-8000-000000009002")
    rubric_id = UUID("00000000-0000-4000-8000-000000009003")
    user_id = UUID("00000000-0000-4000-8000-000000009004")
    attempt_id = UUID("00000000-0000-4000-8000-000000009005")
    answer_id = UUID("00000000-0000-4000-8000-000000009006")
    now = datetime(2026, 7, 18, 8, tzinfo=UTC)
    prompt = "根据材料概括基层治理中存在的问题。"
    first = "基层治理存在多头填报问题。"
    second = "公共服务事项办理效率仍需提升。"
    answer_text = "基层治理存在多头填报，公共服务办理效率不高。"
    context = GroundedReviewContext(
        task=EssayTaskVersion(
            taskVersionId=task_id,
            taskKey="release-evaluation-review",
            versionNo=1,
            questionType="summary",
            prompt=prompt,
            promptSha256=_hash(prompt),
            wordLimit=200,
            maximumScore=20.0,
            state="published",
            createdAt=now,
        ),
        material=EssayMaterialBundleVersion.model_validate(
            {
                "materialBundleVersionId": material_id,
                "taskVersionId": task_id,
                "versionNo": 1,
                "completeness": "complete",
                "sourceKind": "official",
                "contentSha256": _hash(first + second),
                "segments": [
                    {
                        "materialId": "material-1",
                        "segmentId": "segment-1",
                        "materialOrder": 1,
                        "segmentOrder": 1,
                        "text": first,
                        "textSha256": _hash(first),
                    },
                    {
                        "materialId": "material-2",
                        "segmentId": "segment-2",
                        "materialOrder": 2,
                        "segmentOrder": 1,
                        "text": second,
                        "textSha256": _hash(second),
                    },
                ],
                "createdAt": now,
            }
        ),
        rubric=EssayRubricVersion.model_validate(
            {
                "rubricVersionId": rubric_id,
                "taskVersionId": task_id,
                "materialBundleVersionId": material_id,
                "versionNo": 1,
                "sourceKind": "system_compiled" if unverified else "official",
                "verificationStatus": "unverified" if unverified else "verified",
                "state": "published",
                "totalMaximum": 20.0,
                "criteria": [
                    {
                        "criterionCode": "content",
                        "label": "内容",
                        "maximum": 20.0,
                        "scoringRule": "按材料问题要点覆盖评分",
                        "materialEvidenceRequired": True,
                    }
                ],
                "points": [
                    {
                        "rubricPointId": "point-1",
                        "criterionCode": "content",
                        "category": "problem",
                        "label": "多头填报与效率问题",
                        "maximumCredit": 20.0,
                        "materialSegmentIds": ["segment-1", "segment-2"],
                        "materialEvidenceRefs": [
                            {
                                "materialBundleVersionId": material_id,
                                "materialId": "material-1",
                                "segmentId": "segment-1",
                                "startOffset": 0,
                                "endOffset": len(first),
                                "quote": first,
                                "quoteSha256": _hash(first),
                            },
                            {
                                "materialBundleVersionId": material_id,
                                "materialId": "material-2",
                                "segmentId": "segment-2",
                                "startOffset": 0,
                                "endOffset": len(second),
                                "quote": second,
                                "quoteSha256": _hash(second),
                            },
                        ],
                    }
                ],
                "contentSha256": _hash("release-rubric"),
                "createdAt": now,
            }
        ),
        attempt=EssayAttempt(
            attemptId=attempt_id,
            userId=user_id,
            taskVersionId=task_id,
            materialBundleVersionId=material_id,
            rubricVersionId=rubric_id,
            sourceContext="in_program",
            timedStatus="untimed",
            inputFingerprint=_hash("release-attempt"),
            submittedAt=now,
        ),
        answer=EssayAnswerVersion(
            answerVersionId=answer_id,
            attemptId=attempt_id,
            userId=user_id,
            taskVersionId=task_id,
            versionNo=1,
            sourceType="typed",
            answerText=answer_text,
            answerSha256=_hash(answer_text),
            inputFingerprint=_hash("release-answer"),
            createdAt=now,
        ),
    )
    request = EssayReviewRequest(
        attemptId=attempt_id,
        taskVersionId=task_id,
        answerVersionId=answer_id,
        materialBundleVersionId=material_id,
        rubricVersionId=rubric_id,
        reviewScope="grounded_full",
    )
    return context, request


def _release_review(
    suite: GoldenSuite,
    *,
    provider: GroundedEssayReviewProvider,
    price_card: ModelPriceCard,
) -> None:
    operations = ("grounded_review_initial", "grounded_review_repair")
    current = {
        operation: _current_deployment(
            scenario_key=suite.scenario_key,
            operation=operation,
            provider=provider.provider_name,
            model=provider.model,
            prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
            schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
        )
        for operation in operations
    }
    if all(current.values()):
        for operation in operations:
            print(
                f"deployment_ready={current[operation]} "
                f"scenario={suite.scenario_key} operation={operation}"
            )
        return
    observations_by_operation: dict[str, list[EvaluationObservation]] = {
        "grounded_review_initial": [],
        "grounded_review_repair": [],
    }
    for case in suite.cases:
        unverified = case.case_id == "essay-unverified-rubric"
        context, request = _review_fixture(unverified=unverified)
        result = provider.generate(
            context=context,
            request=request,
            idempotency_key=f"release-evaluation:{RELEASE_ID}:{case.case_id}",
        )
        if result.payload is None:
            raise RuntimeError("grounded review evaluation returned no JSON payload")
        candidates = {
            "grounded_review_initial": result,
            "grounded_review_repair": provider.repair(
                context=context,
                request=request,
                rejected_content=json.dumps(
                    result.payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                violation_code="release_repair_revalidation",
                violation_detail="release evaluation requires a full repaired output",
                idempotency_key=(
                    f"release-evaluation:{RELEASE_ID}:{case.case_id}:repair"
                ),
            ),
        }
        for operation, candidate in candidates.items():
            if candidate.payload is None:
                raise RuntimeError(
                    f"{operation} evaluation returned no JSON payload"
                )
            payload = EssayProviderReviewPayload.model_validate_json(
                json.dumps(candidate.payload, ensure_ascii=False)
            )
            generated_at = datetime.now(UTC)
            audit = EssayReviewAudit(
                contractVersion=REVIEW_CONTRACT_VERSION,
                providerSchemaVersion=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
                rubricVersion=RUBRIC_CONTRACT_VERSION,
                rubricSource=context.rubric.source_kind,
                rubricVerification=context.rubric.verification_status,
                promptVersion=GROUNDED_REVIEW_PROMPT_VERSION,
                provider=provider.provider_name,
                model=provider.model,
                inputFingerprint=review_input_fingerprint(context, request),
                outputFingerprint=review_output_fingerprint(payload),
                generatedAt=generated_at,
                validUntil=generated_at + timedelta(days=30),
            )
            GroundedReviewValidator().validate(
                context=context,
                request=request,
                output=payload,
                audit=audit,
            )
            output: dict[str, Any] = {
                "score": {"recommended": payload.score.score_range.recommended},
                "findings": [
                    {"diagnosis": item.diagnosis} for item in payload.findings
                ],
                "limitations": (
                    ["评分规程未经人工金标验证，仅作训练估分。"]
                    if unverified
                    else []
                ),
                "evidenceRefs": [str(item) for item in case.input["facts"]],
            }
            observations_by_operation[operation].append(
                EvaluationObservation(
                    caseId=case.case_id,
                    output=output,
                    costCny=_cost(price_card, candidate.usage),
                )
            )
    for operation in operations:
        _publish(
            suite=suite,
            observations=observations_by_operation[operation],
            operation=operation,
            provider=provider.provider_name,
            model=provider.model,
            prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
            schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
            price_version=price_card.version,
        )


def _rubric_source(case_id: str, prompt: str) -> RubricCompilationSource:
    first = "基层事项多头填报，数据平台彼此隔离。"
    second = "群众办事需要重复提交材料，服务效率较低。"
    return RubricCompilationSource(
        task_version_id=UUID(
            "00000000-0000-4000-8000-000000009101"
            if case_id.endswith("bound")
            else "00000000-0000-4000-8000-000000009102"
        ),
        task_key=f"release-rubric:{case_id}",
        question_type="summary" if case_id.endswith("bound") else "countermeasure",
        prompt=prompt,
        prompt_sha256=_hash(prompt),
        maximum_score=20.0,
        material_bundle_version_id=UUID(
            "00000000-0000-4000-8000-000000009201"
            if case_id.endswith("bound")
            else "00000000-0000-4000-8000-000000009202"
        ),
        material_content_sha256=_hash(first + second),
        segments=(
            RubricSourceSegment("material-1", "segment-1", first, _hash(first)),
            RubricSourceSegment("material-2", "segment-2", second, _hash(second)),
        ),
    )


def _validate_rubric(
    *,
    source: RubricCompilationSource,
    payload: RubricAICriticOutput,
) -> None:
    if (
        not payload.approved
        or payload.task_version_id != source.task_version_id
        or payload.material_bundle_version_id != source.material_bundle_version_id
        or payload.prompt_sha256 != source.prompt_sha256
        or payload.material_content_sha256 != source.material_content_sha256
        or abs(payload.total_maximum - source.maximum_score) > 0.001
    ):
        raise RuntimeError("rubric critic did not approve the exact frozen source")
    by_segment = {item.segment_id: item for item in source.segments}
    criteria = {item.criterion_code: item for item in payload.criteria}
    if abs(sum(item.maximum for item in criteria.values()) - payload.total_maximum) > 0.001:
        raise RuntimeError("rubric criterion scores do not conserve the total")
    seen_spans: set[tuple[str, int, int]] = set()
    for point in payload.points:
        if point.criterion_code not in criteria:
            raise RuntimeError("rubric point references an unknown criterion")
        for evidence in point.material_evidence_refs:
            segment = by_segment.get(evidence.segment_id)
            if segment is None:
                raise RuntimeError("rubric evidence references an unknown segment")
            quote = segment.text[evidence.start_offset : evidence.end_offset]
            if quote != evidence.quote or _hash(quote) != evidence.quote_sha256:
                raise RuntimeError("rubric evidence quote does not match source text")
            key = (evidence.segment_id, evidence.start_offset, evidence.end_offset)
            if key in seen_spans:
                raise RuntimeError("rubric evidence span awards duplicate credit")
            seen_spans.add(key)


def _release_rubric(
    suite: GoldenSuite,
    *,
    provider: GuokaoRubricAIProvider,
    price_card: ModelPriceCard,
) -> None:
    operations = (
        (
            "rubric_generator",
            provider.generator_model,
            RUBRIC_AI_GENERATOR_PROMPT_VERSION,
            RUBRIC_AI_GENERATOR_SCHEMA_VERSION,
        ),
        (
            "rubric_critic",
            provider.critic_model,
            RUBRIC_AI_CRITIC_PROMPT_VERSION,
            RUBRIC_AI_CRITIC_SCHEMA_VERSION,
        ),
    )
    current = {
        operation: _current_deployment(
            scenario_key=suite.scenario_key,
            operation=operation,
            provider=provider.provider_name,
            model=model,
            prompt_version=prompt_version,
            schema_version=schema_version,
        )
        for operation, model, prompt_version, schema_version in operations
    }
    if all(current.values()):
        for operation, _, _, _ in operations:
            print(
                f"deployment_ready={current[operation]} "
                f"scenario={suite.scenario_key} operation={operation}"
            )
        return
    observations: list[EvaluationObservation] = []
    for case in suite.cases:
        source = _rubric_source(case.case_id, str(case.input["prompt"]))
        document = build_rubric_ai_provider_document(source)
        generated = provider.generate(
            document=document,
            idempotency_key=f"release-evaluation:{RELEASE_ID}:{case.case_id}:generator",
        )
        if not isinstance(generated.payload, RubricAIGeneratorOutput):
            raise RuntimeError("rubric generator returned the wrong payload type")
        criticized = provider.criticize_and_revise(
            document=document,
            candidate=generated.payload,
            idempotency_key=f"release-evaluation:{RELEASE_ID}:{case.case_id}:critic",
        )
        if not isinstance(criticized.payload, RubricAICriticOutput):
            raise RuntimeError("rubric critic returned the wrong payload type")
        _validate_rubric(source=source, payload=criticized.payload)
        combined_usage = ModelTokenUsage(
            input_tokens=generated.usage.input_tokens + criticized.usage.input_tokens,
            cached_input_tokens=(
                generated.usage.cached_input_tokens
                + criticized.usage.cached_input_tokens
            ),
            output_tokens=generated.usage.output_tokens + criticized.usage.output_tokens,
        )
        observations.append(
            EvaluationObservation(
                caseId=case.case_id,
                output={
                    "dimensions": [
                        {"name": item.label}
                        for item in criticized.payload.criteria
                    ],
                    "maximumScore": criticized.payload.total_maximum,
                    "limitations": [
                        "系统编制评分规程，不是官方标准答案。",
                        criticized.payload.revision_summary,
                    ],
                    "evidenceRefs": [str(item) for item in case.input["facts"]],
                },
                costCny=_cost(price_card, combined_usage),
            )
        )
    for operation, model, prompt_version, schema_version in operations:
        _publish(
            suite=suite,
            observations=observations,
            operation=operation,
            provider=provider.provider_name,
            model=model,
            prompt_version=prompt_version,
            schema_version=schema_version,
            price_version=price_card.version,
        )


def main() -> None:
    settings = get_settings()
    price_card = ModelPriceCard.from_settings(settings)
    companion = AIProvider(settings)
    cause = CauseAgentProvider(settings)
    review = GroundedEssayReviewProvider(settings)
    rubric = GuokaoRubricAIProvider(settings)
    companion.validate_configuration(idempotency_key="release-configuration-check")
    cause.validate_configuration()
    suites = load_golden_manifest().suites
    for suite in suites:
        if suite.scenario_key in COMPANION_SCENARIOS:
            _release_companion(suite, provider=companion, price_card=price_card)
        elif suite.scenario_key == "xingce_cause_analysis":
            _release_cause(suite, provider=cause, price_card=price_card)
        elif suite.scenario_key == "essay_grounded_review":
            _release_review(suite, provider=review, price_card=price_card)
        elif suite.scenario_key == "essay_rubric_compilation":
            _release_rubric(suite, provider=rubric, price_card=price_card)


if __name__ == "__main__":
    main()
