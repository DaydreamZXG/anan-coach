from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Protocol
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from app.contracts.external_learning.models import RecognitionKind
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    ProcessorNotApproved,
    ProcessorResponseError,
)
from app.domains.external_ingestion.recognition.models import (
    ProcessorApproval,
    ProcessorRequest,
    ProcessorResult,
)


class RecognitionProcessor(Protocol):
    @property
    def provider_release(self) -> str: ...

    def process(self, request: ProcessorRequest) -> ProcessorResult: ...


class ProcessorRegistry:
    """Immutable release registry. Absence, expiry and route drift are all denials."""

    def __init__(
        self,
        approvals: tuple[ProcessorApproval, ...],
        processors: Mapping[str, RecognitionProcessor],
    ) -> None:
        approval_routes = [item.route for item in approvals]
        if len(approval_routes) != len(set(approval_routes)):
            raise ProcessorConfigurationError("processor approval routes must be unique")
        unknown = set(processors) - set(approval_routes)
        if unknown:
            raise ProcessorConfigurationError(
                "processor implementations exist without an approval: "
                + ", ".join(sorted(unknown))
            )
        for approval in approvals:
            processor = processors.get(approval.route)
            if (
                processor is not None
                and processor.provider_release != approval.provider_release
            ):
                raise ProcessorConfigurationError(
                    "processor implementation release does not match its approval"
                )
        self._approvals = {item.route: item for item in approvals}
        self._processors = dict(processors)

    def resolve(
        self,
        *,
        route: str,
        kind: RecognitionKind,
        mime_type: str,
        at,
    ) -> tuple[ProcessorApproval, RecognitionProcessor]:
        approval = self._approvals.get(route)
        processor = self._processors.get(route)
        if approval is None or processor is None:
            raise ProcessorNotApproved("recognition route is not fully approved and configured")
        if not approval.permits(kind=kind, mime_type=mime_type, at=at):
            raise ProcessorNotApproved(
                "recognition route is outside its approved kind, MIME type, or validity window"
            )
        return approval, processor


class HTTPRecognitionProcessor:
    """Strict production adapter for an approved private processor endpoint.

    The adapter intentionally has no local or alternate-provider fallback. The caller
    supplies a release-pinned HTTPS endpoint and credential from the secret store.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        expected_provider_release: str,
        timeout_seconds: float = 45,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ProcessorConfigurationError(
                "recognition processor endpoint must be an absolute HTTPS URL"
            )
        if not bearer_token.strip():
            raise ProcessorConfigurationError("recognition processor credential is empty")
        if not expected_provider_release.strip():
            raise ProcessorConfigurationError("recognition processor release is empty")
        self._endpoint = endpoint.rstrip("/")
        self._token = bearer_token
        self._release = expected_provider_release
        self._client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(timeout_seconds, connect=min(10, timeout_seconds))
            )
        )

    @property
    def provider_release(self) -> str:
        return self._release

    def process(self, request: ProcessorRequest) -> ProcessorResult:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Idempotency-Key": request.idempotency_key,
            "X-Processor-Release": self._release,
        }
        body = request.model_dump(by_alias=True, mode="json")
        try:
            with self._client_factory() as client:
                response = client.post(
                    f"{self._endpoint}/v1/recognize",
                    headers=headers,
                    content=json.dumps(body, ensure_ascii=False, sort_keys=True),
                )
            response.raise_for_status()
            release = response.headers.get("X-Processor-Release", "").strip()
            if release != self._release:
                raise ProcessorResponseError("recognition provider release drifted")
            return ProcessorResult.model_validate(response.json())
        except ProcessorResponseError:
            raise
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise ProcessorResponseError(
                "recognition processor returned no contract-valid result"
            ) from exc
