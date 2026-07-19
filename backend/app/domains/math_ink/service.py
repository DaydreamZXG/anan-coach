from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import time
from uuid import UUID, uuid4

from app.domains.math_ink.provider import MyScriptMathClient, MyScriptResponseError
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
        provider: MyScriptMathClient | None,
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
        self.repository.preflight(input_fingerprint=input_fingerprint)
        if self.provider is None:
            raise MathInkConflict("math ink recognition provider is unavailable")
        started = time.monotonic()
        request_id = uuid4()
        latex = None
        failure_code = None
        try:
            latex, _ = self.provider.recognize(command)
        except MyScriptResponseError as exc:
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
            provider_release=self.repository.settings.myscript_provider_release_id,
            endpoint_hash=sha256(
                self.repository.settings.myscript_endpoint.encode("utf-8")
            ).hexdigest(),
            latex=latex,
            failure_code=failure_code,
            latency_ms=latency_ms,
            created_at=datetime.now(UTC),
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
