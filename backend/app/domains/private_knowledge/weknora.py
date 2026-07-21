from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import httpx

from app.domains.private_knowledge.weknora_upstream import PROJECTION_CHANNEL


class WeKnoraError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False, uncertain: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain


class WeKnoraNotFound(WeKnoraError):
    pass


@dataclass(frozen=True)
class WeKnoraSettings:
    mode: str
    base_url: str
    manage_api_key: str
    ingest_api_key: str
    retrieve_api_key: str
    release_id: str
    https_host_allowlist: tuple[str, ...]
    request_timeout_seconds: float
    search_batch_size: int

    @classmethod
    def from_environment(cls) -> WeKnoraSettings:
        return cls(
            mode=os.getenv("WEKNORA_PRIVATE_KNOWLEDGE_MODE", "canonical").strip(),
            base_url=os.getenv("WEKNORA_BASE_URL", "").strip().rstrip("/"),
            manage_api_key=os.getenv("WEKNORA_MANAGE_API_KEY", "").strip(),
            ingest_api_key=os.getenv("WEKNORA_INGEST_API_KEY", "").strip(),
            retrieve_api_key=os.getenv("WEKNORA_RETRIEVE_API_KEY", "").strip(),
            release_id=os.getenv("WEKNORA_RELEASE_ID", "").strip(),
            https_host_allowlist=tuple(
                item.strip().casefold().rstrip(".")
                for item in os.getenv("WEKNORA_HTTPS_HOST_ALLOWLIST", "").split(",")
                if item.strip()
            ),
            request_timeout_seconds=float(
                os.getenv("WEKNORA_REQUEST_TIMEOUT_SECONDS", "30")
            ),
            search_batch_size=int(os.getenv("WEKNORA_SEARCH_BATCH_SIZE", "500")),
        )

    def validate(self) -> None:
        if self.mode not in {"canonical", "weknora"}:
            raise WeKnoraError("weknora_mode_invalid")
        if self.mode == "canonical":
            return
        parsed = urlsplit(self.base_url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if (
            parsed.scheme.casefold() != "https"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not host
            or host not in self.https_host_allowlist
        ):
            raise WeKnoraError("weknora_base_url_unapproved")
        if (
            not self.manage_api_key
            or not self.ingest_api_key
            or not self.retrieve_api_key
            or not self.release_id
        ):
            raise WeKnoraError("weknora_credentials_incomplete")
        if len({self.manage_api_key, self.ingest_api_key, self.retrieve_api_key}) != 3:
            raise WeKnoraError("weknora_capability_keys_not_separated")
        if not 1 <= self.request_timeout_seconds <= 120:
            raise WeKnoraError("weknora_timeout_invalid")
        if not 1 <= self.search_batch_size <= 1000:
            raise WeKnoraError("weknora_search_batch_invalid")


@dataclass(frozen=True)
class WeKnoraKnowledge:
    knowledge_id: str
    title: str
    parse_status: str


@dataclass(frozen=True)
class WeKnoraCandidate:
    knowledge_id: str
    score: float


class WeKnoraClient:
    def __init__(
        self,
        settings: WeKnoraSettings,
        *,
        client_factory: Callable[..., httpx.Client] | None = None,
    ) -> None:
        settings.validate()
        if settings.mode != "weknora":
            raise WeKnoraError("weknora_client_requires_active_mode")
        self.settings = settings
        self._client_factory = client_factory or httpx.Client

    def list_knowledge_bases(self) -> tuple[dict[str, str], ...]:
        data = self._request(
            "GET",
            "/api/v1/knowledge-bases",
            api_key=self.settings.retrieve_api_key,
        )
        raw = data.get("data")
        if not isinstance(raw, list):
            raise WeKnoraError("weknora_response_invalid")
        values: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise WeKnoraError("weknora_response_invalid")
            identifier = item.get("id")
            name = item.get("name")
            if not isinstance(identifier, str) or not identifier or not isinstance(name, str):
                raise WeKnoraError("weknora_response_invalid")
            values.append({"id": identifier, "name": name})
        return tuple(values)

    def create_knowledge_base(self, *, name: str) -> str:
        data = self._request(
            "POST",
            "/api/v1/knowledge-bases",
            api_key=self.settings.manage_api_key,
            json_body={
                "name": name,
                "description": "Anan account-private derived retrieval projection",
                "type": "document",
                "is_temporary": False,
                "chunking_config": {
                    "chunk_size": 2000,
                    "chunk_overlap": 0,
                    "separators": ["\n\n"],
                    "enable_multimodal": False,
                    "enable_parent_child": False,
                },
                "question_generation_config": {"enabled": False, "question_count": 0},
            },
            outcome_uncertain=True,
        )
        payload = _object(data.get("data"))
        return _non_empty_string(payload.get("id"))

    def delete_knowledge_base(self, knowledge_base_id: str) -> None:
        self._request(
            "DELETE",
            f"/api/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}",
            api_key=self.settings.manage_api_key,
            outcome_uncertain=True,
        )

    def get_knowledge_base(self, knowledge_base_id: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/api/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}",
            api_key=self.settings.retrieve_api_key,
        )
        return _object(data.get("data"))

    def list_knowledge(self, knowledge_base_id: str) -> tuple[WeKnoraKnowledge, ...]:
        values: list[WeKnoraKnowledge] = []
        page = 1
        page_size = 200
        while True:
            data = self._request(
                "GET",
                f"/api/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}"
                f"/knowledge?page={page}&page_size={page_size}",
                api_key=self.settings.retrieve_api_key,
            )
            raw = data.get("data")
            response_page = data.get("page")
            response_page_size = data.get("page_size")
            total = data.get("total")
            if (
                not isinstance(raw, list)
                or isinstance(response_page, bool)
                or not isinstance(response_page, int)
                or isinstance(response_page_size, bool)
                or not isinstance(response_page_size, int)
                or isinstance(total, bool)
                or not isinstance(total, int)
                or response_page != page
                or response_page_size <= 0
                or total < 0
            ):
                raise WeKnoraError("weknora_response_invalid")
            values.extend(_knowledge(item) for item in raw)
            if len(values) >= total:
                if len(values) != total:
                    raise WeKnoraError("weknora_response_invalid")
                return tuple(values)
            if not raw:
                raise WeKnoraError("weknora_response_invalid")
            page += 1

    def create_manual_knowledge(
        self, *, knowledge_base_id: str, title: str, content: str
    ) -> WeKnoraKnowledge:
        data = self._request(
            "POST",
            f"/api/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}/knowledge/manual",
            api_key=self.settings.ingest_api_key,
            json_body={
                "title": title,
                "content": content,
                "status": "published",
                "channel": PROJECTION_CHANNEL,
            },
            outcome_uncertain=True,
        )
        return _knowledge(data.get("data"), expected_title=title)

    def get_knowledge(self, knowledge_id: str) -> WeKnoraKnowledge:
        data = self._request(
            "GET",
            f"/api/v1/knowledge/{quote(knowledge_id, safe='')}",
            api_key=self.settings.retrieve_api_key,
        )
        return _knowledge(data.get("data"))

    def delete_knowledge(self, knowledge_id: str) -> None:
        self._request(
            "DELETE",
            f"/api/v1/knowledge/{quote(knowledge_id, safe='')}",
            api_key=self.settings.ingest_api_key,
            outcome_uncertain=True,
        )

    def hybrid_search(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        knowledge_ids: tuple[str, ...],
        match_count: int,
    ) -> tuple[WeKnoraCandidate, ...]:
        if not knowledge_ids:
            return ()
        data = self._request(
            "POST",
            f"/api/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}/hybrid-search",
            api_key=self.settings.retrieve_api_key,
            json_body={
                "query_text": query,
                "match_count": match_count,
                "knowledge_ids": list(knowledge_ids),
                "skip_context_enrichment": True,
            },
        )
        raw = data.get("data")
        if not isinstance(raw, list):
            raise WeKnoraError("weknora_response_invalid")
        candidates: list[WeKnoraCandidate] = []
        for item in raw:
            payload = _object(item)
            knowledge_id = _non_empty_string(payload.get("knowledge_id"))
            score = payload.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise WeKnoraError("weknora_response_invalid")
            candidates.append(
                WeKnoraCandidate(knowledge_id=knowledge_id, score=float(score))
            )
        return tuple(candidates)

    def _request(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        json_body: dict[str, Any] | None = None,
        outcome_uncertain: bool = False,
    ) -> dict[str, Any]:
        try:
            with self._client_factory(
                timeout=httpx.Timeout(self.settings.request_timeout_seconds, connect=10.0),
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method,
                    f"{self.settings.base_url}{path}",
                    headers={
                        "X-API-Key": api_key,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=json_body,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise WeKnoraError(
                "weknora_network_unavailable",
                retryable=not outcome_uncertain,
                uncertain=outcome_uncertain,
            ) from exc
        if response.is_redirect:
            raise WeKnoraError("weknora_redirect_rejected")
        if response.status_code == 404:
            raise WeKnoraNotFound("weknora_not_found")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WeKnoraError(
                "weknora_http_rejected",
                retryable=response.status_code >= 500,
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeKnoraError("weknora_response_invalid") from exc
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise WeKnoraError("weknora_response_invalid")
        return payload


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WeKnoraError("weknora_response_invalid")
    return value


def _non_empty_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise WeKnoraError("weknora_response_invalid")
    return value


def _knowledge(value: object, *, expected_title: str | None = None) -> WeKnoraKnowledge:
    payload = _object(value)
    knowledge_id = _non_empty_string(payload.get("id"))
    title = _non_empty_string(payload.get("title"))
    parse_status = _non_empty_string(payload.get("parse_status"))
    if parse_status not in {
        "pending",
        "processing",
        "finalizing",
        "completed",
        "failed",
        "cancelled",
    }:
        raise WeKnoraError("weknora_response_invalid")
    if expected_title is not None and title != expected_title:
        raise WeKnoraError("weknora_response_invalid")
    return WeKnoraKnowledge(
        knowledge_id=knowledge_id, title=title, parse_status=parse_status
    )
