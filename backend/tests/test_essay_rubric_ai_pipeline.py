from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.model_cost import ModelPriceCard, ModelTokenUsage
from app.domains.essay_intelligence.rubric_ai_contracts import (
    RubricAICriticOutput,
    RubricAIGeneratorOutput,
    build_rubric_ai_provider_document,
)
from app.domains.essay_intelligence.rubric_ai_pipeline import (
    GuokaoRubricAIPipeline,
    RubricAIPipelineError,
    _acquire_task_lock,
    _release_task_lock,
    rubric_ai_task_lock_key,
)
from app.domains.essay_intelligence.rubric_ai_provider import (
    GuokaoRubricAIProvider,
    RubricAIProviderConfigurationError,
    RubricAIProviderError,
    RubricAIProviderResult,
)
from app.domains.essay_intelligence.rubric_compiler import (
    EssayRubricCompilationError,
    RubricCompilationSource,
)
from app.domains.essay_intelligence.rubric_publisher import (
    load_rubric_compilation_source,
)


def test_provider_sends_only_full_product_source_and_two_strict_calls() -> None:
    source = _memory_source()
    document = build_rubric_ai_provider_document(source)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_payload = json.loads(body["messages"][1]["content"])
        seen.append(
            {
                "body": body,
                "user": user_payload,
                "idempotency": request.headers["x-idempotency-key"],
            }
        )
        phase = body["response_format"]["json_schema"]["name"].rsplit("_", 1)[-1]
        if phase == "generator":
            output = _generator_payload(source)
        else:
            output = _critic_payload(source, _generator_payload(source))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 10},
                },
            },
            headers={"x-request-id": "raw-provider-request-id"},
        )

    provider = GuokaoRubricAIProvider(
        _provider_settings(),
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )
    generated = provider.generate(
        document=document,
        idempotency_key="stable-generator-key",
    )
    assert isinstance(generated.payload, RubricAIGeneratorOutput)
    criticized = provider.criticize_and_revise(
        document=document,
        candidate=generated.payload,
        idempotency_key="stable-critic-key",
    )

    assert isinstance(criticized.payload, RubricAICriticOutput)
    assert [item["idempotency"] for item in seen] == [
        "stable-generator-key",
        "stable-critic-key",
    ]
    for captured in seen:
        serialized = json.dumps(captured["user"], ensure_ascii=False)
        assert "userId" not in serialized
        assert "answer" not in serialized.casefold()
        assert "考生" not in serialized
        assert all(segment.text in serialized for segment in source.segments)
        assert captured["body"]["response_format"]["json_schema"]["strict"] is True
        assert captured["body"]["response_format"]["json_schema"]["schema"] == (
            captured["user"]["requiredOutputSchema"]
        )
    assert "generatorCandidate" not in seen[0]["user"]
    assert "generatorCandidate" in seen[1]["user"]
    assert generated.provider_request_id_sha256 == _hash(
        "raw-provider-request-id"
    )
    assert not hasattr(generated, "provider_request_id")


def test_production_provider_requires_a_separate_versioned_rubric_release() -> None:
    source = _memory_source()
    settings = replace(
        _provider_settings(),
        environment="production",
        ai_essay_rubric_provider_release_id="",
        ai_essay_review_provider_release_id="review-release-v1",
        ai_essay_review_provider_https_host_allowlist="provider.example",
        ai_essay_review_provider_dpa_confirmed=True,
        ai_essay_review_provider_idempotency_capability="replayable_queryable",
    )
    provider = GuokaoRubricAIProvider(settings)

    with pytest.raises(
        RubricAIProviderConfigurationError,
        match="AI_ESSAY_RUBRIC_PROVIDER_RELEASE_ID",
    ):
        provider.generate(
            document=build_rubric_ai_provider_document(source),
            idempotency_key="stable-generator-key",
        )


def test_provider_marks_successful_response_without_usage_as_retryable() -> None:
    source = _memory_source()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(_generator_payload(source))
                        }
                    }
                ]
            },
        )

    provider = GuokaoRubricAIProvider(
        _provider_settings(),
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(RubricAIProviderError) as error:
        provider.generate(
            document=build_rubric_ai_provider_document(source),
            idempotency_key="stable-generator-key",
        )

    assert error.value.code == "rubric_provider_response_invalid"
    assert error.value.retryable is True


def test_billed_invalid_structured_response_records_cost_without_publish(
    db_session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(db_session_factory, suffix="billed-invalid-schema")
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 10},
                },
            },
            headers={"x-request-id": "billed-invalid-request"},
        )

    settings = _provider_settings()
    provider = GuokaoRubricAIProvider(
        settings,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )
    pipeline = GuokaoRubricAIPipeline(
        settings=settings,
        provider=provider,
        price_card=_price_card(),
    )

    with db_session_factory() as session:
        with pytest.raises(RubricAIPipelineError) as error:
            pipeline.run_source(session, source=source, dry_run=False)
    assert error.value.code == "rubric_provider_response_invalid"
    assert request_count == 1

    with db_session_factory() as session:
        call = session.execute(
            text(
                "select status, input_tokens, cached_input_tokens, "
                "output_tokens, total_cost_cny, output_fingerprint "
                "from ai_audit.model_call_ledger"
            )
        ).mappings().one()
        assert call["status"] == "succeeded"
        assert call["input_tokens"] == 100
        assert call["cached_input_tokens"] == 10
        assert call["output_tokens"] == 20
        assert call["total_cost_cny"] > 0
        assert len(call["output_fingerprint"]) == 64
        assert session.execute(
            text("select count(*) from product.essay_rubric_versions")
        ).scalar_one() == 0


@pytest.mark.parametrize(
    "error_code",
    ["rubric_provider_transport_failed", "rubric_provider_response_invalid"],
)
def test_retryable_generator_failure_replays_with_original_price_snapshot(
    db_session_factory: sessionmaker[Session],
    error_code: str,
) -> None:
    source = _seed_source(
        db_session_factory,
        suffix=f"retry-{error_code}",
    )
    provider = FakeRubricProvider(
        source=source,
        generator_retryable_error_code=error_code,
    )
    original_price = _price_card()
    first_pipeline = _pipeline(provider, price_card=original_price)

    with db_session_factory() as session:
        with pytest.raises(RubricAIPipelineError) as error:
            first_pipeline.run_source(session, source=source, dry_run=True)
    assert error.value.code == error_code

    with db_session_factory() as session:
        running = session.execute(
            text(
                "select operation, status, price_version, "
                "input_rate_cny_per_million, "
                "cached_input_rate_cny_per_million, "
                "output_rate_cny_per_million "
                "from ai_audit.model_call_ledger"
            )
        ).mappings().one()
        assert running == {
            "operation": "rubric_generator",
            "status": "running",
            "price_version": original_price.version,
            "input_rate_cny_per_million": Decimal("2"),
            "cached_input_rate_cny_per_million": Decimal("0.5"),
            "output_rate_cny_per_million": Decimal("4"),
        }

    replacement_price = ModelPriceCard(
        version="test-price-v2",
        input_cny_per_million=Decimal("20"),
        cached_input_cny_per_million=Decimal("5"),
        output_cny_per_million=Decimal("40"),
    )
    second_pipeline = _pipeline(provider, price_card=replacement_price)
    with db_session_factory() as session:
        result = second_pipeline.run_source(session, source=source, dry_run=True)

    assert result.generator_replayed is False
    assert result.critic_replayed is False
    assert provider.phases == ["generator", "generator", "critic"]
    assert provider.idempotency_keys[0] == provider.idempotency_keys[1]
    with db_session_factory() as session:
        calls = session.execute(
            text(
                "select operation, status, price_version, total_cost_cny "
                "from ai_audit.model_call_ledger order by operation"
            )
        ).mappings().all()
        assert [item["operation"] for item in calls] == [
            "rubric_critic",
            "rubric_generator",
        ]
        assert [item["status"] for item in calls] == ["succeeded", "succeeded"]
        by_operation = {item["operation"]: item for item in calls}
        assert by_operation["rubric_generator"]["price_version"] == (
            original_price.version
        )
        assert by_operation["rubric_generator"]["total_cost_cny"] == (
            original_price.cost(provider.usage).total_cost_cny
        )
        assert by_operation["rubric_critic"]["price_version"] == (
            replacement_price.version
        )
        assert by_operation["rubric_critic"]["total_cost_cny"] == (
            replacement_price.cost(provider.usage).total_cost_cny
        )


def test_pipeline_dry_run_replay_cost_audit_and_append_only_publish(
    db_session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(db_session_factory, suffix="success")
    provider = FakeRubricProvider(source=source)
    pipeline = _pipeline(provider)

    with db_session_factory() as session:
        pending = pipeline.discover_pending_sources(session)
        assert [item.task_version_id for item in pending] == [source.task_version_id]
        assert pipeline.discover_pending_sources(
            session,
            task_version_ids=(source.task_version_id,),
        ) == (source,)
        assert pipeline.discover_pending_sources(
            session,
            task_version_ids=(uuid4(),),
        ) == ()
        dry_run = pipeline.run_source(session, source=source, dry_run=True)
    assert dry_run.publish_result.versions[0].action == "would_insert"
    assert provider.phases == ["generator", "critic"]

    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from product.essay_rubric_versions")
        ).scalar_one() == 0
        calls = session.execute(
            text(
                "select call_id, operation, status, output_fingerprint, "
                "total_cost_cny, completed_at "
                "from ai_audit.model_call_ledger order by operation"
            )
        ).mappings().all()
        ledger_snapshot = [dict(item) for item in calls]
        assert [item["operation"] for item in calls] == [
            "rubric_critic",
            "rubric_generator",
        ]
        assert all(item["status"] == "succeeded" for item in calls)
        assert all(item["total_cost_cny"] > 0 for item in calls)

    with db_session_factory() as session:
        published = pipeline.run_source(session, source=source, dry_run=False)
    assert published.generator_replayed is True
    assert published.critic_replayed is True
    assert provider.phases == ["generator", "critic", "generator", "critic"]
    assert provider.idempotency_keys[0] == provider.idempotency_keys[2]
    assert provider.idempotency_keys[1] == provider.idempotency_keys[3]

    with db_session_factory() as session:
        unchanged = pipeline.run_source(session, source=source, dry_run=False)
    assert unchanged.publish_result.inserted_count == 0
    assert unchanged.publish_result.unchanged_count == 1
    assert unchanged.publish_result.versions[0].action == "unchanged"
    assert unchanged.generator_replayed is True
    assert unchanged.critic_replayed is True
    assert provider.phases == [
        "generator",
        "critic",
        "generator",
        "critic",
        "generator",
        "critic",
    ]
    assert provider.idempotency_keys[0] == provider.idempotency_keys[4]
    assert provider.idempotency_keys[1] == provider.idempotency_keys[5]

    with db_session_factory() as session:
        rubric = session.execute(
            text(
                "select source_kind, verification_status, compilation_audit "
                "from product.essay_rubric_versions"
            )
        ).mappings().one()
        assert rubric["source_kind"] == "system_compiled"
        assert rubric["verification_status"] == "unverified"
        audit = rubric["compilation_audit"]["pipelineAudit"]
        assert audit["criticApproved"] is True
        assert audit["generator"]["providerRequestIdSha256"] == _hash(
            "generator-request-id"
        )
        assert audit["critic"]["providerRequestIdSha256"] == _hash(
            "critic-request-id"
        )
        serialized_audit = json.dumps(audit)
        assert "generator-request-id" not in serialized_audit
        assert "critic-request-id" not in serialized_audit
        assert session.execute(
            text("select count(*) from ai_audit.model_call_ledger")
        ).scalar_one() == 2
        replayed_ledger = session.execute(
            text(
                "select call_id, operation, status, output_fingerprint, "
                "total_cost_cny, completed_at "
                "from ai_audit.model_call_ledger order by operation"
            )
        ).mappings().all()
        assert [dict(item) for item in replayed_ledger] == ledger_snapshot
        assert pipeline.discover_pending_sources(session) == ()


def test_critic_rejection_is_audited_and_never_published(
    db_session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(db_session_factory, suffix="rejected")
    provider = FakeRubricProvider(source=source, critic_approved=False)

    with db_session_factory() as session:
        with pytest.raises(RubricAIPipelineError, match="Critic rejected") as error:
            _pipeline(provider).run_source(session, source=source, dry_run=False)
    assert error.value.code == "rubric_critic_rejected"
    assert provider.phases == ["generator", "critic"]
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from product.essay_rubric_versions")
        ).scalar_one() == 0
        assert session.execute(
            text(
                "select count(*) from ai_audit.model_call_ledger "
                "where status = 'succeeded'"
            )
        ).scalar_one() == 2


@pytest.mark.parametrize("invalid", ["quote", "score"])
def test_deterministic_compiler_blocks_invalid_approved_critic_output(
    db_session_factory: sessionmaker[Session],
    invalid: str,
) -> None:
    source = _seed_source(db_session_factory, suffix=f"invalid-{invalid}")
    provider = FakeRubricProvider(source=source, invalid_final=invalid)

    with db_session_factory() as session:
        with pytest.raises(EssayRubricCompilationError):
            _pipeline(provider).run_source(session, source=source, dry_run=False)
    assert provider.phases == ["generator", "critic"]
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from product.essay_rubric_versions")
        ).scalar_one() == 0
        assert session.execute(
            text("select count(*) from ai_audit.model_call_ledger")
        ).scalar_one() == 2


def test_stable_provider_key_returning_changed_output_hard_fails(
    db_session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(db_session_factory, suffix="replay-change")
    provider = FakeRubricProvider(source=source)
    pipeline = _pipeline(provider)
    with db_session_factory() as session:
        pipeline.run_source(session, source=source, dry_run=True)

    provider.change_generator_replay = True
    with db_session_factory() as session:
        with pytest.raises(RubricAIPipelineError) as error:
            pipeline.run_source(session, source=source, dry_run=False)
    assert error.value.code == "rubric_provider_replay_mismatch"
    assert provider.phases == ["generator", "critic", "generator"]
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from product.essay_rubric_versions")
        ).scalar_one() == 0
        assert session.execute(
            text("select count(*) from ai_audit.model_call_ledger")
        ).scalar_one() == 2


def test_task_lock_is_exclusive_and_independent_of_provider_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    task_version_id = uuid4()
    lock_key = rubric_ai_task_lock_key(task_version_id)
    assert lock_key == f"essay-rubric-ai:task:{task_version_id}"

    with db_session_factory() as session:
        engine = session.get_bind()
        with engine.connect() as first, engine.connect() as second:
            _acquire_task_lock(first, lock_key=lock_key)
            second_acquired = bool(
                second.execute(
                    text(
                        "select pg_try_advisory_lock("
                        "hashtextextended(:lock_key, 0))"
                    ),
                    {"lock_key": lock_key},
                ).scalar_one()
            )
            assert second_acquired is False
            second.rollback()

            _release_task_lock(first, lock_key=lock_key)
            assert bool(
                second.execute(
                    text(
                        "select pg_try_advisory_lock("
                        "hashtextextended(:lock_key, 0))"
                    ),
                    {"lock_key": lock_key},
                ).scalar_one()
            ) is True
            _release_task_lock(second, lock_key=lock_key)


class FakeRubricProvider:
    provider_name = "fake-guokao-rubric-provider@release-1"
    generator_model = "fake-generator-v1"
    critic_model = "fake-critic-v1"

    def __init__(
        self,
        *,
        source: RubricCompilationSource,
        critic_approved: bool = True,
        invalid_final: str | None = None,
        generator_retryable_error_code: str | None = None,
    ) -> None:
        self.source = source
        self.critic_approved = critic_approved
        self.invalid_final = invalid_final
        self.generator_retryable_error_code = generator_retryable_error_code
        self.change_generator_replay = False
        self.phases: list[str] = []
        self.idempotency_keys: list[str] = []
        self.usage = ModelTokenUsage(
            input_tokens=100,
            cached_input_tokens=10,
            output_tokens=20,
        )

    def generate(self, *, document, idempotency_key: str) -> RubricAIProviderResult:
        self.phases.append("generator")
        self.idempotency_keys.append(idempotency_key)
        if self.generator_retryable_error_code is not None:
            error_code = self.generator_retryable_error_code
            self.generator_retryable_error_code = None
            raise RubricAIProviderError(
                error_code,
                "Retryable generator failure",
                retryable=True,
            )
        payload = RubricAIGeneratorOutput.model_validate_json(
            json.dumps(_generator_payload(self.source))
        )
        variation = "changed" if self.change_generator_replay else "stable"
        return _result(
            payload,
            fingerprint_seed=f"generator:{variation}",
            request_id="generator-request-id",
        )

    def criticize_and_revise(
        self,
        *,
        document,
        candidate: RubricAIGeneratorOutput,
        idempotency_key: str,
    ) -> RubricAIProviderResult:
        self.phases.append("critic")
        self.idempotency_keys.append(idempotency_key)
        raw = _critic_payload(
            self.source,
            candidate.model_dump(by_alias=True, mode="json"),
            approved=self.critic_approved,
        )
        if self.invalid_final == "quote":
            evidence = raw["points"][0]["materialEvidenceRefs"][0]
            evidence.update(
                {
                    "startOffset": 0,
                    "endOffset": 2,
                    "quote": "虚假",
                    "quoteSha256": _hash("虚假"),
                }
            )
        elif self.invalid_final == "score":
            raw["criteria"][0]["maximum"] = self.source.maximum_score - 1
        payload = RubricAICriticOutput.model_validate_json(json.dumps(raw))
        return _result(
            payload,
            fingerprint_seed="critic:stable",
            request_id="critic-request-id",
        )


def _pipeline(
    provider: FakeRubricProvider,
    *,
    price_card: ModelPriceCard | None = None,
) -> GuokaoRubricAIPipeline:
    return GuokaoRubricAIPipeline(
        settings=Settings(),
        provider=provider,
        price_card=price_card or _price_card(),
    )


def _price_card() -> ModelPriceCard:
    return ModelPriceCard(
        version="test-price-v1",
        input_cny_per_million=Decimal("2"),
        cached_input_cny_per_million=Decimal("0.5"),
        output_cny_per_million=Decimal("4"),
    )


def _result(payload, *, fingerprint_seed: str, request_id: str):
    return RubricAIProviderResult(
        payload=payload,
        usage=ModelTokenUsage(
            input_tokens=100,
            cached_input_tokens=10,
            output_tokens=20,
        ),
        latency_ms=12,
        output_fingerprint=_hash(fingerprint_seed),
        provider_request_id_sha256=_hash(request_id),
    )


def _generator_payload(source: RubricCompilationSource) -> dict[str, Any]:
    segment = source.segments[0]
    quote = "多头填报"
    start = segment.text.index(quote)
    return {
        "schemaVersion": "guokao-essay-rubric-generator-schema-v1",
        "taskVersionId": str(source.task_version_id),
        "materialBundleVersionId": str(source.material_bundle_version_id),
        "promptSha256": source.prompt_sha256,
        "materialContentSha256": source.material_content_sha256,
        "totalMaximum": source.maximum_score,
        "criteria": [
            {
                "criterionCode": "content",
                "label": "内容要点",
                "maximum": source.maximum_score,
                "scoringRule": "只按材料原文锚定要点计分。",
                "materialEvidenceRequired": True,
            }
        ],
        "points": [
            {
                "rubricPointId": "point-1",
                "criterionCode": "content",
                "category": "problem",
                "label": "多头填报",
                "maximumCredit": source.maximum_score,
                "materialSegmentIds": [segment.segment_id],
                "materialEvidenceRefs": [
                    {
                        "materialBundleVersionId": str(
                            source.material_bundle_version_id
                        ),
                        "materialId": segment.material_id,
                        "segmentId": segment.segment_id,
                        "startOffset": start,
                        "endOffset": start + len(quote),
                        "quote": quote,
                        "quoteSha256": _hash(quote),
                    }
                ],
            }
        ],
    }


def _critic_payload(
    source: RubricCompilationSource,
    candidate: dict[str, Any],
    *,
    approved: bool = True,
) -> dict[str, Any]:
    return {
        "schemaVersion": "guokao-essay-rubric-critic-schema-v1",
        "approved": approved,
        "rejectionCodes": [] if approved else ["evidence_not_sufficient"],
        "revisionSummary": "逐项复核并输出完整终稿。",
        "taskVersionId": str(source.task_version_id),
        "materialBundleVersionId": str(source.material_bundle_version_id),
        "promptSha256": source.prompt_sha256,
        "materialContentSha256": source.material_content_sha256,
        "totalMaximum": source.maximum_score,
        "criteria": deepcopy(candidate["criteria"]),
        "points": deepcopy(candidate["points"]),
    }


def _memory_source() -> RubricCompilationSource:
    prompt = "请概括材料反映的问题，不超过200字。（20分）"
    first = "基层事项多头填报，数据平台彼此隔离。"
    second = "群众办事需要重复提交材料。"
    from app.domains.essay_intelligence.rubric_compiler import RubricSourceSegment

    return RubricCompilationSource(
        task_version_id=UUID("00000000-0000-0000-0000-000000000301"),
        task_key="guokao-essay:provider-test",
        question_type="summary",
        prompt=prompt,
        prompt_sha256=_hash(prompt),
        maximum_score=20.0,
        material_bundle_version_id=UUID(
            "00000000-0000-0000-0000-000000000302"
        ),
        material_content_sha256=_hash(first + second),
        segments=(
            RubricSourceSegment("material-1", "segment-1", first, _hash(first)),
            RubricSourceSegment("material-2", "segment-2", second, _hash(second)),
        ),
    )


def _seed_source(
    factory: sessionmaker[Session],
    *,
    suffix: str,
) -> RubricCompilationSource:
    task_id = uuid4()
    material_id = uuid4()
    task_key = f"guokao-essay:ai-{suffix}"
    prompt = "请概括材料反映的问题，不超过200字。（20分）"
    segments = [
        ("material-1", "segment-1", "基层事项多头填报，数据平台彼此隔离。"),
        ("material-2", "segment-2", "群众办事需要重复提交材料。"),
    ]
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.essay_task_versions (
                  task_version_id, task_key, version_no, question_type,
                  prompt, prompt_sha256, word_limit, maximum_score, state
                ) values (
                  cast(:task_id as uuid), :task_key, 1, 'summary',
                  :prompt, :prompt_sha256, 200, 20, 'published'
                )
                """
            ),
            {
                "task_id": str(task_id),
                "task_key": task_key,
                "prompt": prompt,
                "prompt_sha256": _hash(prompt),
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_material_bundle_versions (
                  material_bundle_version_id, task_version_id, task_key,
                  version_no, completeness, source_kind, content_sha256
                ) values (
                  cast(:material_id as uuid), cast(:task_id as uuid), :task_key,
                  1, 'complete', 'official', :content_sha256
                )
                """
            ),
            {
                "material_id": str(material_id),
                "task_id": str(task_id),
                "task_key": task_key,
                "content_sha256": _hash("".join(item[2] for item in segments)),
            },
        )
        for order, (material_key, segment_id, segment_text) in enumerate(
            segments,
            start=1,
        ):
            session.execute(
                text(
                    """
                    insert into product.essay_material_segments (
                      material_segment_id, material_bundle_version_id,
                      material_id, segment_id, material_order, segment_order,
                      text, text_sha256
                    ) values (
                      cast(:row_id as uuid), cast(:material_id as uuid),
                      :material_key, :segment_id, :material_order, 1,
                      :text, :text_sha256
                    )
                    """
                ),
                {
                    "row_id": str(uuid4()),
                    "material_id": str(material_id),
                    "material_key": material_key,
                    "segment_id": segment_id,
                    "material_order": order,
                    "text": segment_text,
                    "text_sha256": _hash(segment_text),
                },
            )
    with factory() as session:
        source = load_rubric_compilation_source(
            session,
            task_version_id=str(task_id),
            material_bundle_version_id=str(material_id),
        )
    assert source is not None
    return source


def _provider_settings() -> Settings:
    return Settings(
        ai_provider_base_url="https://provider.example/v1",
        ai_api_key="test-api-key",
        ai_essay_rubric_generator_model="generator-model-v1",
        ai_essay_rubric_critic_model="critic-model-v1",
        ai_essay_rubric_provider_release_id="rubric-release-v1",
        ai_essay_review_provider_idempotency_key_header="X-Idempotency-Key",
        ai_essay_review_provider_request_id_header="X-Request-ID",
        ai_essay_rubric_max_input_bytes=1_000_000,
        ai_essay_rubric_max_output_bytes=1_000_000,
    )


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
