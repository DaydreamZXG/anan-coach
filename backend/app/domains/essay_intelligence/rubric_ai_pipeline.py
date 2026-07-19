from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ai.model_cost import (
    ModelCallLedgerRepository,
    ModelCallRecord,
    ModelPriceCard,
)
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.ai.governance.repository import (
    AIGovernanceRepository,
    generic_evidence_ref,
)
from app.domains.essay_intelligence.rubric_ai_contracts import (
    RUBRIC_AI_COMPILATION_PROMPT_VERSION,
    RUBRIC_AI_COMPILER_VERSION,
    RUBRIC_AI_CRITIC_PROMPT_VERSION,
    RUBRIC_AI_CRITIC_SCHEMA_VERSION,
    RUBRIC_AI_GENERATOR_PROMPT_VERSION,
    RUBRIC_AI_GENERATOR_SCHEMA_VERSION,
    RUBRIC_AI_PIPELINE_VERSION,
    RubricAICriticOutput,
    RubricAIGeneratorOutput,
    RubricAIProviderDocument,
    build_rubric_ai_provider_document,
    rubric_ai_critic_input_fingerprint,
    rubric_ai_generator_input_fingerprint,
)
from app.domains.essay_intelligence.rubric_ai_provider import (
    GuokaoRubricAIProvider,
    RubricAIMeteredProviderError,
    RubricAIProviderConfigurationError,
    RubricAIProviderError,
    RubricAIProviderResult,
)
from app.domains.essay_intelligence.rubric_compiler import (
    RUBRIC_COMPILATION_CONTRACT_VERSION,
    RubricAICompilationCallAudit,
    RubricAIPipelineCompilationAudit,
    RubricCompilationSource,
    SystemRubricCompilationDraft,
    compile_system_rubric,
    rubric_input_fingerprint,
    validate_rubric_compilation_source,
)
from app.domains.essay_intelligence.rubric_publisher import (
    EssayRubricPublishResult,
    GuokaoEssayRubricPublisher,
    load_rubric_compilation_source,
)


RUBRIC_AI_FEATURE = "essay_rubric_compilation"


class RubricAIPipelineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RubricAIProviderProtocol(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def generator_model(self) -> str: ...

    @property
    def critic_model(self) -> str: ...

    def generate(
        self,
        *,
        document: RubricAIProviderDocument,
        idempotency_key: str,
    ) -> RubricAIProviderResult: ...

    def criticize_and_revise(
        self,
        *,
        document: RubricAIProviderDocument,
        candidate: RubricAIGeneratorOutput,
        idempotency_key: str,
    ) -> RubricAIProviderResult: ...


@dataclass(frozen=True)
class RubricAICallOutcome:
    record: ModelCallRecord
    result: RubricAIProviderResult
    replayed: bool


@dataclass(frozen=True)
class RubricAITaskRunResult:
    task_version_id: str
    material_bundle_version_id: str
    generator_call_id: str
    critic_call_id: str
    generator_replayed: bool
    critic_replayed: bool
    publish_result: EssayRubricPublishResult


class GuokaoRubricAIPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: RubricAIProviderProtocol | None = None,
        ledger: ModelCallLedgerRepository | None = None,
        price_card: ModelPriceCard | None = None,
        publisher: GuokaoEssayRubricPublisher | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or GuokaoRubricAIProvider(settings)
        self._ledger = ledger or ModelCallLedgerRepository()
        self._price_card = price_card or ModelPriceCard.from_settings(settings)
        self._publisher = publisher or GuokaoEssayRubricPublisher()

    def discover_pending_sources(
        self,
        session: Session,
        *,
        task_version_ids: tuple[UUID, ...] | None = None,
    ) -> tuple[RubricCompilationSource, ...]:
        if task_version_ids == ():
            return ()
        filter_sql = ""
        params: dict[str, object] = {}
        if task_version_ids is not None:
            filter_sql = (
                " and task.task_version_id = any(cast(:task_version_ids as uuid[]))"
            )
            params["task_version_ids"] = [str(item) for item in task_version_ids]
        rows = session.execute(
            text(
                """
                select task.task_version_id, material.material_bundle_version_id
                  from product.essay_task_versions task
                  join product.essay_material_bundle_versions material
                    on material.task_version_id = task.task_version_id
                   and material.completeness = 'complete'
                 where task.state = 'published'
                   and task.task_key like 'guokao%'
                   and not exists (
                     select 1 from product.essay_task_versions successor
                      where successor.task_key = task.task_key
                        and successor.version_no > task.version_no
                        and successor.state = 'published'
                   )
                   and not exists (
                     select 1 from product.essay_material_bundle_versions successor
                      where successor.task_version_id = material.task_version_id
                        and successor.version_no > material.version_no
                        and successor.completeness = 'complete'
                   )
                   and not exists (
                     select 1 from product.essay_rubric_versions rubric
                      where rubric.task_version_id = task.task_version_id
                        and rubric.material_bundle_version_id =
                            material.material_bundle_version_id
                        and rubric.state = 'published'
                        and not exists (
                          select 1 from product.essay_rubric_versions successor
                           where successor.supersedes_rubric_version_id =
                                 rubric.rubric_version_id
                             and successor.task_version_id = rubric.task_version_id
                             and successor.material_bundle_version_id =
                                 rubric.material_bundle_version_id
                        )
                   )
                """
                + filter_sql
                + " order by task.task_key, task.version_no"
            ),
            params,
        ).mappings().all()
        sources: list[RubricCompilationSource] = []
        for row in rows:
            source = load_rubric_compilation_source(
                session,
                task_version_id=str(row["task_version_id"]),
                material_bundle_version_id=str(row["material_bundle_version_id"]),
            )
            if source is None:
                raise RubricAIPipelineError(
                    "rubric_pending_source_changed",
                    "Pending task no longer has a current complete source",
                )
            sources.append(source)
        return tuple(sources)

    def run_source(
        self,
        session: Session,
        *,
        source: RubricCompilationSource,
        dry_run: bool,
    ) -> RubricAITaskRunResult:
        validate_rubric_compilation_source(source)
        lock_key = rubric_ai_task_lock_key(source.task_version_id)
        bind = session.get_bind()
        if not isinstance(bind, Engine):
            raise RubricAIPipelineError(
                "rubric_database_bind_invalid",
                "Rubric AI pipeline requires an engine-backed session",
            )
        lock_connection = bind.connect()
        lock_acquired = False
        try:
            _acquire_task_lock(lock_connection, lock_key=lock_key)
            lock_acquired = True
            current_source = load_rubric_compilation_source(
                session,
                task_version_id=str(source.task_version_id),
                material_bundle_version_id=str(source.material_bundle_version_id),
            )
            if current_source is None or current_source != source:
                raise RubricAIPipelineError(
                    "rubric_source_not_current",
                    "Rubric AI accepts only the exact current published Guokao task "
                    "and its latest complete material",
                )
            source = current_source
            provider_name = self._provider.provider_name
            document = build_rubric_ai_provider_document(source)
            generator_input = rubric_ai_generator_input_fingerprint(
                document=document,
                provider=provider_name,
                model=self._provider.generator_model,
            )
            generator = self._execute_call(
                session,
                phase="generator",
                model=self._provider.generator_model,
                prompt_version=RUBRIC_AI_GENERATOR_PROMPT_VERSION,
                schema_version=RUBRIC_AI_GENERATOR_SCHEMA_VERSION,
                input_fingerprint=generator_input,
                invoke=lambda key: self._provider.generate(
                    document=document,
                    idempotency_key=key,
                ),
            )
            if not isinstance(generator.result.payload, RubricAIGeneratorOutput):
                raise RubricAIPipelineError(
                    "rubric_generator_payload_type_invalid",
                    "Generator returned the wrong structured payload type",
                )
            candidate = generator.result.payload
            _require_source_binding(source=source, payload=candidate)

            critic_input = rubric_ai_critic_input_fingerprint(
                document=document,
                candidate=candidate,
                provider=provider_name,
                model=self._provider.critic_model,
            )
            critic = self._execute_call(
                session,
                phase="critic",
                model=self._provider.critic_model,
                prompt_version=RUBRIC_AI_CRITIC_PROMPT_VERSION,
                schema_version=RUBRIC_AI_CRITIC_SCHEMA_VERSION,
                input_fingerprint=critic_input,
                invoke=lambda key: self._provider.criticize_and_revise(
                    document=document,
                    candidate=candidate,
                    idempotency_key=key,
                ),
            )
            if not isinstance(critic.result.payload, RubricAICriticOutput):
                raise RubricAIPipelineError(
                    "rubric_critic_payload_type_invalid",
                    "Critic returned the wrong structured payload type",
                )
            final = critic.result.payload
            _require_source_binding(source=source, payload=final)
            if not final.approved:
                raise RubricAIPipelineError(
                    "rubric_critic_rejected",
                    "Critic rejected the complete revised rubric: "
                    + ",".join(final.rejection_codes),
                )

            model_release = (
                f"generator={self._provider.generator_model};"
                f"critic={self._provider.critic_model}"
            )
            compilation_input = rubric_input_fingerprint(
                source=source,
                compiler_version=RUBRIC_AI_COMPILER_VERSION,
                prompt_version=RUBRIC_AI_COMPILATION_PROMPT_VERSION,
                provider=provider_name,
                model=model_release,
            )
            draft = SystemRubricCompilationDraft(
                contract_version=RUBRIC_COMPILATION_CONTRACT_VERSION,
                task_version_id=source.task_version_id,
                material_bundle_version_id=source.material_bundle_version_id,
                prompt_sha256=source.prompt_sha256,
                material_content_sha256=source.material_content_sha256,
                compiler_version=RUBRIC_AI_COMPILER_VERSION,
                prompt_version=RUBRIC_AI_COMPILATION_PROMPT_VERSION,
                provider=provider_name,
                model=model_release,
                input_fingerprint=compilation_input,
                source_kind="system_compiled",
                verification_status="unverified",
                total_maximum=final.total_maximum,
                criteria=final.criteria,
                points=final.points,
                pipeline_audit=RubricAIPipelineCompilationAudit(
                    pipeline_version=RUBRIC_AI_PIPELINE_VERSION,
                    generator=_call_audit(
                        outcome=generator,
                        prompt_version=RUBRIC_AI_GENERATOR_PROMPT_VERSION,
                        schema_version=RUBRIC_AI_GENERATOR_SCHEMA_VERSION,
                        provider=provider_name,
                        model=self._provider.generator_model,
                        input_fingerprint=generator_input,
                    ),
                    critic=_call_audit(
                        outcome=critic,
                        prompt_version=RUBRIC_AI_CRITIC_PROMPT_VERSION,
                        schema_version=RUBRIC_AI_CRITIC_SCHEMA_VERSION,
                        provider=provider_name,
                        model=self._provider.critic_model,
                        input_fingerprint=critic_input,
                    ),
                    critic_approved=True,
                ),
            )
            # The deterministic compiler is the authority. AI approval alone never
            # reaches publication.
            compile_system_rubric(source=source, draft=draft)
            try:
                publish_result = self._publisher.publish(
                    session,
                    drafts=[draft],
                    dry_run=dry_run,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise
            return RubricAITaskRunResult(
                task_version_id=str(source.task_version_id),
                material_bundle_version_id=str(source.material_bundle_version_id),
                generator_call_id=str(generator.record.call_id),
                critic_call_id=str(critic.record.call_id),
                generator_replayed=generator.replayed,
                critic_replayed=critic.replayed,
                publish_result=publish_result,
            )
        finally:
            session.rollback()
            try:
                if lock_acquired:
                    _release_task_lock(lock_connection, lock_key=lock_key)
            finally:
                lock_connection.close()

    def _execute_call(
        self,
        session: Session,
        *,
        phase: Literal["generator", "critic"],
        model: str,
        prompt_version: str,
        schema_version: str,
        input_fingerprint: str,
        invoke: Callable[[str], RubricAIProviderResult],
    ) -> RubricAICallOutcome:
        call_key = f"essay-rubric-ai:{phase}:{input_fingerprint}"
        record = self._ledger.begin(
            session,
            call_key=call_key,
            user_id=None,
            job_id=None,
            feature=RUBRIC_AI_FEATURE,
            operation=f"rubric_{phase}",
            provider=self._provider.provider_name,
            model=model,
            prompt_version=prompt_version,
            schema_version=schema_version,
            price_card=self._price_card,
            input_fingerprint=input_fingerprint,
            attribution=AIGovernanceRepository().attribution(
                session,
                environment=self._settings.environment,
                scenario_key="essay_rubric_compilation",
                operation=f"rubric_{phase}",
                provider=self._provider.provider_name,
                model=model,
                prompt_version=prompt_version,
                schema_version=schema_version,
                evidence_refs=(
                    generic_evidence_ref(
                        kind="essay_rubric_compilation_input",
                        source_id=input_fingerprint,
                        version=input_fingerprint,
                    ),
                ),
                retrieval_bundle_id=None,
                reservation_id=None,
                reservation_source=None,
            ),
            processor_release_id=(
                self._settings.ai_essay_rubric_provider_release_id.strip()
            ),
            processor_endpoint_fingerprint=endpoint_fingerprint(
                self._settings.ai_provider_base_url
            ),
            processing_purpose=f"rubric_{phase}",
            processing_data_categories=("public_exam_material",),
        )
        session.commit()
        if record.status == "failed":
            raise RubricAIPipelineError(
                "rubric_model_call_previously_failed",
                f"Stable {phase} model call already failed; change the release to retry",
            )
        provider_key = f"essay-rubric-{phase}-{input_fingerprint}"
        try:
            result = invoke(provider_key)
        except RubricAIMeteredProviderError as exc:
            if record.status == "succeeded":
                if record.output_fingerprint != exc.output_fingerprint:
                    raise RubricAIPipelineError(
                        "rubric_provider_replay_mismatch",
                        f"Provider changed the {phase} response for a stable "
                        "idempotency key",
                    ) from exc
            else:
                self._ledger.succeed(
                    session,
                    call_id=record.call_id,
                    usage=exc.usage,
                    output_fingerprint=exc.output_fingerprint,
                    latency_ms=exc.latency_ms,
                )
                session.commit()
            raise RubricAIPipelineError(exc.code, str(exc)) from exc
        except RubricAIProviderError as exc:
            error_code = exc.code
            if record.status == "running" and not exc.retryable:
                self._ledger.fail(
                    session,
                    call_id=record.call_id,
                    error_code=error_code,
                    latency_ms=0,
                )
                session.commit()
            else:
                session.rollback()
            raise RubricAIPipelineError(error_code, str(exc)) from exc
        except RubricAIProviderConfigurationError as exc:
            # Configuration is not an outcome of the logical provider call. Keep
            # the committed reservation open so corrected configuration can replay
            # the same provider key without fabricating a terminal zero-cost row.
            session.rollback()
            raise RubricAIPipelineError(
                "rubric_provider_configuration_invalid",
                str(exc),
            ) from exc
        except Exception as exc:
            # An exception crossing the provider boundary is billing-ambiguous.
            # Preserve the stable logical call for an idempotent replay; only an
            # explicit non-retryable provider error is safe to terminalize.
            session.rollback()
            raise RubricAIPipelineError(
                "rubric_provider_unexpected_failure",
                f"Rubric {phase} provider failed unexpectedly",
            ) from exc

        if record.status == "succeeded":
            if record.output_fingerprint != result.output_fingerprint:
                raise RubricAIPipelineError(
                    "rubric_provider_replay_mismatch",
                    f"Provider changed the {phase} response for a stable idempotency key",
                )
            return RubricAICallOutcome(
                record=record,
                result=result,
                replayed=True,
            )
        succeeded = self._ledger.succeed(
            session,
            call_id=record.call_id,
            usage=result.usage,
            output_fingerprint=result.output_fingerprint,
            latency_ms=result.latency_ms,
        )
        session.commit()
        return RubricAICallOutcome(
            record=succeeded,
            result=result,
            replayed=False,
        )


def _require_source_binding(
    *,
    source: RubricCompilationSource,
    payload: RubricAIGeneratorOutput | RubricAICriticOutput,
) -> None:
    if (
        payload.task_version_id != source.task_version_id
        or payload.material_bundle_version_id != source.material_bundle_version_id
        or payload.prompt_sha256 != source.prompt_sha256
        or payload.material_content_sha256 != source.material_content_sha256
        or abs(payload.total_maximum - source.maximum_score) > 0.001
    ):
        raise RubricAIPipelineError(
            "rubric_provider_source_binding_mismatch",
            "Provider rubric is not bound to the exact current task and complete material",
        )


def _call_audit(
    *,
    outcome: RubricAICallOutcome,
    prompt_version: str,
    schema_version: str,
    provider: str,
    model: str,
    input_fingerprint: str,
) -> RubricAICompilationCallAudit:
    return RubricAICompilationCallAudit(
        call_id=outcome.record.call_id,
        prompt_version=prompt_version,
        schema_version=schema_version,
        provider=provider,
        model=model,
        input_fingerprint=input_fingerprint,
        output_fingerprint=outcome.result.output_fingerprint,
        provider_request_id_sha256=(
            outcome.result.provider_request_id_sha256
        ),
    )


def rubric_ai_task_lock_key(task_version_id: UUID) -> str:
    """One lock namespace per task, independent of model or provider release."""
    return f"essay-rubric-ai:task:{task_version_id}"


def _acquire_task_lock(connection: Connection, *, lock_key: str) -> None:
    connection.execute(
        text("select pg_advisory_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )


def _release_task_lock(connection: Connection, *, lock_key: str) -> None:
    released = bool(
        connection.execute(
            text("select pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        ).scalar_one()
    )
    connection.commit()
    if not released:
        raise RubricAIPipelineError(
            "rubric_task_lock_lost",
            "Rubric AI task lock was not held by this database session",
        )
