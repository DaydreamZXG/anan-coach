from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ai.governance.contracts import AICallAttribution
from app.domains.ai.governance.repository import AIGovernanceRepository
from app.domains.ai.external_processing import ExternalProcessingRepository


MILLION = Decimal("1000000")
COST_QUANTUM = Decimal("0.00000001")


class ModelCostConfigurationError(RuntimeError):
    pass


class ModelCallLedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelTokenUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.cached_input_tokens, self.output_tokens) < 0:
            raise ValueError("Model token usage cannot be negative")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached input tokens cannot exceed input tokens")


@dataclass(frozen=True)
class ModelPriceCard:
    version: str
    input_cny_per_million: Decimal
    cached_input_cny_per_million: Decimal
    output_cny_per_million: Decimal

    def __post_init__(self) -> None:
        if not self.version.strip() or self.version != self.version.strip():
            raise ModelCostConfigurationError(
                "AI price version must be a non-empty trimmed value"
            )
        rates = (
            self.input_cny_per_million,
            self.cached_input_cny_per_million,
            self.output_cny_per_million,
        )
        if any(not isinstance(rate, Decimal) or not rate.is_finite() for rate in rates):
            raise ModelCostConfigurationError("AI token prices must be finite decimals")
        if (
            self.input_cny_per_million <= 0
            or self.output_cny_per_million <= 0
            or self.cached_input_cny_per_million < 0
        ):
            raise ModelCostConfigurationError(
                "AI input/output prices must be positive and cached price non-negative"
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> "ModelPriceCard":
        if not settings.ai_price_version.strip():
            raise ModelCostConfigurationError("AI_PRICE_VERSION is required")
        try:
            input_rate = Decimal(settings.ai_input_cny_per_million_tokens)
            cached_rate = Decimal(
                settings.ai_cached_input_cny_per_million_tokens
            )
            output_rate = Decimal(settings.ai_output_cny_per_million_tokens)
        except (InvalidOperation, ValueError) as exc:
            raise ModelCostConfigurationError(
                "All AI token prices must be configured as decimal CNY values"
            ) from exc
        return cls(
            version=settings.ai_price_version.strip(),
            input_cny_per_million=input_rate,
            cached_input_cny_per_million=cached_rate,
            output_cny_per_million=output_rate,
        )

    def cost(self, usage: ModelTokenUsage) -> "ModelCallCost":
        uncached = usage.input_tokens - usage.cached_input_tokens
        input_cost = _money(
            Decimal(uncached) * self.input_cny_per_million / MILLION
        )
        cached_cost = _money(
            Decimal(usage.cached_input_tokens)
            * self.cached_input_cny_per_million
            / MILLION
        )
        output_cost = _money(
            Decimal(usage.output_tokens) * self.output_cny_per_million / MILLION
        )
        return ModelCallCost(
            input_cost_cny=input_cost,
            cached_input_cost_cny=cached_cost,
            output_cost_cny=output_cost,
            total_cost_cny=input_cost + cached_cost + output_cost,
        )


@dataclass(frozen=True)
class ModelCallCost:
    input_cost_cny: Decimal
    cached_input_cost_cny: Decimal
    output_cost_cny: Decimal
    total_cost_cny: Decimal


@dataclass(frozen=True)
class ModelCallRecord:
    call_id: UUID
    call_key: str
    user_id: UUID | None
    job_id: UUID | None
    feature: str
    operation: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    status: str
    input_fingerprint: str
    output_fingerprint: str | None
    error_code: str | None
    price_card: ModelPriceCard


class ModelCallLedgerRepository:
    """Privacy-safe ledger: content never enters this table, only fingerprints and usage."""

    def begin(
        self,
        session: Session,
        *,
        call_key: str,
        user_id: str | None,
        job_id: UUID | None,
        feature: str,
        operation: str,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        price_card: ModelPriceCard,
        input_fingerprint: str,
        attribution: AICallAttribution,
        processor_release_id: str | None = None,
        processor_endpoint_fingerprint: str | None = None,
        processing_purpose: str | None = None,
        processing_data_categories: tuple[str, ...] = (),
        started_at: datetime | None = None,
    ) -> ModelCallRecord:
        values = {
            "call_id": str(uuid4()),
            "call_key": call_key,
            "user_id": user_id,
            "job_id": str(job_id) if job_id else None,
            "feature": feature,
            "operation": operation,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "price_version": price_card.version,
            "input_rate_cny_per_million": price_card.input_cny_per_million,
            "cached_input_rate_cny_per_million": (
                price_card.cached_input_cny_per_million
            ),
            "output_rate_cny_per_million": price_card.output_cny_per_million,
            "input_fingerprint": input_fingerprint,
            "started_at": started_at or datetime.now(UTC),
        }
        row = session.execute(
            text(
                """
                insert into ai_audit.model_call_ledger (
                  call_id, call_key, user_id, job_id, feature, operation,
                  provider, model, prompt_version, schema_version, price_version,
                  input_rate_cny_per_million,
                  cached_input_rate_cny_per_million,
                  output_rate_cny_per_million,
                  input_fingerprint, status, started_at
                ) values (
                  cast(:call_id as uuid), :call_key, cast(:user_id as uuid),
                  cast(:job_id as uuid), :feature, :operation, :provider, :model,
                  :prompt_version, :schema_version, :price_version,
                  :input_rate_cny_per_million,
                  :cached_input_rate_cny_per_million,
                  :output_rate_cny_per_million,
                  :input_fingerprint, 'running', :started_at
                )
                on conflict (call_key) do nothing
                returning *
                """
            ),
            values,
        ).mappings().first()
        if row is None:
            existing = self.get_existing(
                session,
                call_key=call_key,
                user_id=user_id,
                job_id=job_id,
                feature=feature,
                operation=operation,
                input_fingerprint=input_fingerprint,
            )
            if existing is None:
                raise ModelCallLedgerError(
                    "Model call insert conflicted without an existing ledger row"
                )
            runtime_identity = (
                existing.provider == provider,
                existing.model == model,
                existing.prompt_version == prompt_version,
                existing.schema_version == schema_version,
            )
            if not all(runtime_identity):
                raise ModelCallLedgerError(
                    "An existing model call key refers to a different provider contract"
                )
            AIGovernanceRepository().insert_model_call_attribution(
                session,
                call_id=existing.call_id,
                attribution=attribution,
            )
            self._record_processing_receipt(
                session,
                record=existing,
                attribution=attribution,
                processor_release_id=processor_release_id,
                processor_endpoint_fingerprint=processor_endpoint_fingerprint,
                processing_purpose=processing_purpose,
                processing_data_categories=processing_data_categories,
            )
            return existing
        record = _record(row)
        AIGovernanceRepository().insert_model_call_attribution(
            session,
            call_id=record.call_id,
            attribution=attribution,
        )
        self._record_processing_receipt(
            session,
            record=record,
            attribution=attribution,
            processor_release_id=processor_release_id,
            processor_endpoint_fingerprint=processor_endpoint_fingerprint,
            processing_purpose=processing_purpose,
            processing_data_categories=processing_data_categories,
        )
        return record

    def _record_processing_receipt(
        self,
        session: Session,
        *,
        record: ModelCallRecord,
        attribution: AICallAttribution,
        processor_release_id: str | None,
        processor_endpoint_fingerprint: str | None,
        processing_purpose: str | None,
        processing_data_categories: tuple[str, ...],
    ) -> None:
        production = attribution.evaluation_state == "passed"
        if production and (
            not processor_release_id
            or not processor_endpoint_fingerprint
            or not processing_purpose
            or not processing_data_categories
        ):
            raise ModelCallLedgerError(
                "production model calls require an explicit processor release receipt"
            )
        ExternalProcessingRepository().record_model_dispatch(
            session,
            call_id=record.call_id,
            user_id=str(record.user_id) if record.user_id is not None else None,
            scenario_key=attribution.scenario_key,
            purpose=processing_purpose or record.operation,
            data_categories=processing_data_categories or ("synthetic_test",),
            declared_provider_release=processor_release_id or record.provider,
            provider_endpoint_fingerprint=(
                processor_endpoint_fingerprint
                or sha256(record.provider.encode("utf-8")).hexdigest()
            ),
            input_fingerprint=record.input_fingerprint,
            production_authorization_required=production,
        )

    def get_existing(
        self,
        session: Session,
        *,
        call_key: str,
        user_id: str | None,
        job_id: UUID | None,
        feature: str,
        operation: str,
        input_fingerprint: str,
    ) -> ModelCallRecord | None:
        row = session.execute(
            text(
                "select * from ai_audit.model_call_ledger "
                "where call_key = :call_key"
            ),
            {"call_key": call_key},
        ).mappings().one_or_none()
        if row is None:
            return None
        immutable = {
            "user_id": UUID(user_id) if user_id else None,
            "job_id": job_id,
            "feature": feature,
            "operation": operation,
            "input_fingerprint": input_fingerprint,
        }
        if any(row[key] != value for key, value in immutable.items()):
            raise ModelCallLedgerError(
                "An existing model call key refers to different immutable input"
            )
        return _record(row)

    def completed_for_checkpoint(
        self,
        session: Session,
        *,
        call_id: UUID,
        job_id: UUID,
        input_fingerprint: str,
        output_fingerprint: str,
    ) -> ModelCallRecord:
        row = session.execute(
            text(
                """
                select * from ai_audit.model_call_ledger
                 where call_id = cast(:call_id as uuid)
                   and job_id = cast(:job_id as uuid)
                   and status = 'succeeded'
                   and input_fingerprint = :input_fingerprint
                   and output_fingerprint = :output_fingerprint
                """
            ),
            {
                "call_id": str(call_id),
                "job_id": str(job_id),
                "input_fingerprint": input_fingerprint,
                "output_fingerprint": output_fingerprint,
            },
        ).mappings().one_or_none()
        if row is None:
            raise ModelCallLedgerError(
                "Review checkpoint is not backed by a matching completed model call"
            )
        return _record(row)

    def terminal_for_checkpoint(
        self,
        session: Session,
        *,
        call_id: UUID,
        job_id: UUID,
        input_fingerprint: str,
    ) -> ModelCallRecord:
        row = session.execute(
            text(
                """
                select * from ai_audit.model_call_ledger
                 where call_id = cast(:call_id as uuid)
                   and job_id = cast(:job_id as uuid)
                   and status in ('succeeded', 'failed')
                   and input_fingerprint = :input_fingerprint
                """
            ),
            {
                "call_id": str(call_id),
                "job_id": str(job_id),
                "input_fingerprint": input_fingerprint,
            },
        ).mappings().one_or_none()
        if row is None:
            raise ModelCallLedgerError(
                "Checkpoint is not backed by a matching terminal model call"
            )
        return _record(row)

    def succeed(
        self,
        session: Session,
        *,
        call_id: UUID,
        usage: ModelTokenUsage,
        output_fingerprint: str,
        latency_ms: int,
        completed_at: datetime | None = None,
    ) -> ModelCallRecord:
        snapshot = session.execute(
            text(
                "select * from ai_audit.model_call_ledger "
                "where call_id = cast(:call_id as uuid) and status = 'running' "
                "for update"
            ),
            {"call_id": str(call_id)},
        ).mappings().one_or_none()
        if snapshot is None:
            raise ModelCallLedgerError("Only a running model call can succeed")
        cost = _record(snapshot).price_card.cost(usage)
        row = session.execute(
            text(
                """
                update ai_audit.model_call_ledger
                   set status = 'succeeded', output_fingerprint = :output_fingerprint,
                       input_tokens = :input_tokens,
                       cached_input_tokens = :cached_input_tokens,
                       output_tokens = :output_tokens,
                       input_cost_cny = :input_cost_cny,
                       cached_input_cost_cny = :cached_input_cost_cny,
                       output_cost_cny = :output_cost_cny,
                       total_cost_cny = :total_cost_cny,
                       latency_ms = :latency_ms, error_code = null,
                       completed_at = :completed_at
                 where call_id = cast(:call_id as uuid) and status = 'running'
                 returning *
                """
            ),
            {
                "call_id": str(call_id),
                "output_fingerprint": output_fingerprint,
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
                "input_cost_cny": cost.input_cost_cny,
                "cached_input_cost_cny": cost.cached_input_cost_cny,
                "output_cost_cny": cost.output_cost_cny,
                "total_cost_cny": cost.total_cost_cny,
                "latency_ms": latency_ms,
                "completed_at": completed_at or datetime.now(UTC),
            },
        ).mappings().first()
        if row is None:
            raise ModelCallLedgerError("Only a running model call can succeed")
        return _record(row)

    def fail(
        self,
        session: Session,
        *,
        call_id: UUID,
        error_code: str,
        latency_ms: int,
        completed_at: datetime | None = None,
    ) -> ModelCallRecord:
        row = session.execute(
            text(
                """
                update ai_audit.model_call_ledger
                   set status = 'failed', error_code = :error_code,
                       latency_ms = :latency_ms, completed_at = :completed_at
                 where call_id = cast(:call_id as uuid) and status = 'running'
                 returning *
                """
            ),
            {
                "call_id": str(call_id),
                "error_code": error_code,
                "latency_ms": latency_ms,
                "completed_at": completed_at or datetime.now(UTC),
            },
        ).mappings().first()
        if row is None:
            raise ModelCallLedgerError("Only a running model call can fail")
        return _record(row)


def _record(row: RowMapping) -> ModelCallRecord:
    return ModelCallRecord(
        call_id=row["call_id"],
        call_key=str(row["call_key"]),
        user_id=row["user_id"],
        job_id=row["job_id"],
        feature=str(row["feature"]),
        operation=str(row["operation"]),
        provider=str(row["provider"]),
        model=str(row["model"]),
        prompt_version=str(row["prompt_version"]),
        schema_version=str(row["schema_version"]),
        status=str(row["status"]),
        input_fingerprint=str(row["input_fingerprint"]),
        output_fingerprint=(
            str(row["output_fingerprint"])
            if row["output_fingerprint"] is not None
            else None
        ),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        price_card=ModelPriceCard(
            version=str(row["price_version"]),
            input_cny_per_million=Decimal(str(row["input_rate_cny_per_million"])),
            cached_input_cny_per_million=Decimal(
                str(row["cached_input_rate_cny_per_million"])
            ),
            output_cny_per_million=Decimal(str(row["output_rate_cny_per_million"])),
        ),
    )


def _money(value: Decimal) -> Decimal:
    """Match PostgreSQL NUMERIC(14,8) rounding before conserving the total."""

    return value.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)
