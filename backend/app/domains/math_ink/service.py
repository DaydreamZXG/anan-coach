from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import time
from uuid import UUID, uuid4

from app.domains.math_ink.provider import (
    MathInkProviderDescriptor,
    MathInkProviderResponseError,
    MathInkProviderResult,
    MyScriptMathClient,
    XfyunMathInkClient,
)
from app.domains.math_ink.repository import MathInkConflict, MathInkRepository
from app.domains.math_ink.schemas import (
    ConfirmFormulaCommand,
    FormulaVersion,
    MathInkRecognitionResult,
    RecognizeMathInkCommand,
)


class MathInkService:
    def __init__(
        self,
        repository: MathInkRepository,
        provider: MyScriptMathClient | XfyunMathInkClient | None,
    ) -> None:
        self.repository = repository
        self.provider = provider

    def recognize(
        self,
        *,
        owner_user_id: str,
        command: RecognizeMathInkCommand,
        idempotency_key: str,
    ) -> MathInkRecognitionResult:
        payload = command.model_dump(by_alias=True, mode="json")
        input_fingerprint = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        replay = self.repository.replay(
            owner_user_id=owner_user_id, idempotency_key=idempotency_key
        )
        if replay is not None:
            if str(replay["input_fingerprint"]) != input_fingerprint:
                raise MathInkConflict("math ink idempotency key changed input")
            return self.repository.replay_projection(replay)
        if not self.repository.owned_active_question(
            owner_user_id=owner_user_id,
            session_id=command.session_id,
            question_id=command.question_id,
        ):
            raise MathInkConflict(
                "math ink must bind the current user's active task question"
            )
        if self.provider is None:
            raise MathInkConflict("math ink recognition provider is unavailable")
        descriptor = self._descriptor()
        self.repository.preflight(
            input_fingerprint=input_fingerprint,
            provider=descriptor,
        )
        started = time.monotonic()
        request_id = uuid4()
        latex = None
        recognition_metadata = None
        failure_code = None
        try:
            provider_result = self.provider.recognize(command)
            if isinstance(provider_result, MathInkProviderResult):
                latex = provider_result.latex
                recognition_metadata = {
                    "contractVersion": "formula-recognition-metadata@v1",
                    "provider": descriptor.provider,
                    "candidateSource": descriptor.candidate_source,
                    "providerRelease": descriptor.provider_release,
                    "modelRelease": descriptor.model_release,
                    "priceVersion": descriptor.price_version,
                    "unitPriceCny": descriptor.unit_price_cny,
                    "deploymentId": descriptor.deployment_id or None,
                    "rendererVersion": provider_result.renderer_version,
                    "confidence": provider_result.confidence,
                    "providerRequestIdFingerprint": (
                        sha256(
                            provider_result.provider_request_id.encode("utf-8")
                        ).hexdigest()
                        if provider_result.provider_request_id
                        else None
                    ),
                    "vectorFingerprint": provider_result.vector_fingerprint,
                    "renderFingerprint": provider_result.render_fingerprint,
                }
            else:
                latex, _ = provider_result
        except MathInkProviderResponseError as exc:
            failure_code = exc.code
        latency_ms = int((time.monotonic() - started) * 1000)
        return self.repository.insert_result(
            request_id=request_id,
            owner_user_id=owner_user_id,
            session_id=command.session_id,
            question_id=command.question_id,
            idempotency_key=idempotency_key,
            input_fingerprint=input_fingerprint,
            ink_payload=payload,
            provider=descriptor,
            endpoint_hash=sha256(descriptor.endpoint.encode("utf-8")).hexdigest(),
            latex=latex,
            recognition_metadata=recognition_metadata,
            failure_code=failure_code,
            latency_ms=latency_ms,
            created_at=datetime.now(UTC),
        )

    def _descriptor(self) -> MathInkProviderDescriptor:
        descriptor = getattr(self.provider, "descriptor", None)
        if isinstance(descriptor, MathInkProviderDescriptor):
            return descriptor
        settings = self.repository.settings
        return MathInkProviderDescriptor(
            provider="MyScript",
            scenario_key="myscript_math_recognition",
            candidate_source="myscript_candidate",
            provider_release=settings.myscript_provider_release_id,
            model_release=settings.myscript_model_release,
            endpoint=settings.myscript_endpoint,
            price_version=settings.myscript_price_version,
            unit_price_cny=settings.myscript_unit_price_cny,
            deployment_id=settings.myscript_deployment_id,
        )

    def confirm(
        self,
        *,
        owner_user_id: str,
        formula_version_id: UUID,
        command: ConfirmFormulaCommand,
    ) -> FormulaVersion:
        return self.repository.confirm(
            owner_user_id=owner_user_id,
            formula_version_id=formula_version_id,
            expected_version_no=command.expected_version_no,
            latex=command.latex,
            now=datetime.now(UTC),
        )
