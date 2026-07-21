from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Protocol
from uuid import UUID, uuid4

import yaml

from app.domains.private_knowledge.repository import (
    PrivateKnowledgeConflict,
    PrivateKnowledgeRepository,
)
from app.domains.private_knowledge.projection import (
    PrivateKnowledgeProjectionGateway,
    projection_error_from_provider,
)
from app.domains.private_knowledge.weknora import WeKnoraError
from app.domains.private_knowledge.schemas import (
    CreatePrivateSourceCommand,
    MarkdownExport,
    PrivateSearchResult,
    PrivateSourceDeletionView,
    PrivateSourceList,
    PrivateSourceSummary,
)


class PrivateMarkdownReader(Protocol):
    def open(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        maximum_bytes: int,
        prefix: str,
        suffix: str = "",
    ) -> AbstractContextManager[Path]: ...


class PrivateKnowledgeService:
    def __init__(
        self,
        repository: PrivateKnowledgeRepository,
        *,
        markdown_reader: PrivateMarkdownReader | None = None,
        projection_gateway: PrivateKnowledgeProjectionGateway | None = None,
    ) -> None:
        self.repository = repository
        self.markdown_reader = markdown_reader
        self.projection_gateway = projection_gateway

    def create(
        self,
        *,
        owner_user_id: str,
        command: CreatePrivateSourceCommand,
        idempotency_key: str,
    ) -> PrivateSourceSummary:
        command_type = "source_import" if command.source_id else "source_create"
        request_fingerprint = _fingerprint(
            command.model_dump(by_alias=True, mode="json")
        )
        replay = self.repository.command(
            owner_user_id=owner_user_id,
            command_type=command_type,
            key=idempotency_key,
        )
        if replay is not None:
            if str(replay["request_fingerprint"]) != request_fingerprint:
                raise PrivateKnowledgeConflict(
                    "private source idempotency key changed immutable input"
                )
            return self.repository.summary(
                owner_user_id=owner_user_id,
                source_version_id=UUID(str(replay["source_version_id"])),
            )
        artifact = self.repository.artifact(
            owner_user_id=owner_user_id,
            artifact_version_id=command.artifact_version_id,
        )
        if artifact is None:
            raise PrivateKnowledgeConflict(
                "private source artifact is unavailable or belongs to another account"
            )
        mime_type = str(artifact["mime_type"])
        recognition_id = command.recognition_revision_id
        if mime_type == "text/markdown":
            if recognition_id is not None:
                raise PrivateKnowledgeConflict(
                    "Markdown imports cannot bind an OCR recognition revision"
                )
            markdown = self._read_markdown(artifact)
            metadata, markdown = _split_frontmatter(markdown)
        else:
            if recognition_id is None:
                raise PrivateKnowledgeConflict(
                    "image and PDF sources require a confirmed OCR revision"
                )
            recognition = self.repository.confirmed_recognition(
                owner_user_id=owner_user_id,
                recognition_revision_id=recognition_id,
                artifact_version_id=command.artifact_version_id,
            )
            if recognition is None:
                raise PrivateKnowledgeConflict(
                    "private source OCR must be the current confirmed owner revision"
                )
            metadata = {}
            markdown = _markdown_from_recognition(
                dict(recognition["candidate_payload"])
            )
        head = None
        source_id = command.source_id or uuid4()
        version_no = 1
        supersedes = None
        if command.source_id is not None:
            head = self.repository.head(
                owner_user_id=owner_user_id, source_id=command.source_id
            )
            if head is None:
                raise PrivateKnowledgeConflict(
                    "private source import target does not exist"
                )
            if int(head["version_no"]) != command.expected_version_no:
                raise PrivateKnowledgeConflict(
                    "private source changed after the exported Markdown version"
                )
            _validate_round_trip_metadata(metadata, head)
            source_id = UUID(str(head["source_id"]))
            version_no = int(head["version_no"]) + 1
            supersedes = UUID(str(head["source_version_id"]))
        elif any(key.startswith("anan_") for key in metadata):
            raise PrivateKnowledgeConflict(
                "versioned Markdown must name an existing source import target"
            )
        markdown = _canonical_markdown(metadata, markdown)
        pages = _pages(markdown)
        source_version_id = self.repository.insert_source(
            owner_user_id=owner_user_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            source_id=source_id,
            version_no=version_no,
            title=command.title,
            document_kind=command.document_kind,
            artifact_version_id=command.artifact_version_id,
            recognition_revision_id=recognition_id,
            markdown=markdown,
            supersedes_source_version_id=supersedes,
            pages=pages,
            created_at=datetime.now(UTC),
        )
        if self.projection_gateway is not None:
            self.projection_gateway.enqueue_source(
                owner_user_id=owner_user_id,
                source_version_id=source_version_id,
            )
        return self.repository.summary(
            owner_user_id=owner_user_id, source_version_id=source_version_id
        )

    def list(self, *, owner_user_id: str) -> PrivateSourceList:
        return self.repository.list_current(owner_user_id=owner_user_id)

    def search(
        self, *, owner_user_id: str, query: str, maximum_sources: int = 8
    ) -> PrivateSearchResult:
        normalized = query.strip()
        if not normalized:
            raise PrivateKnowledgeConflict("private knowledge search query is blank")
        if self.projection_gateway is not None:
            try:
                return self.projection_gateway.search(
                    owner_user_id=owner_user_id,
                    query=normalized,
                    maximum_sources=maximum_sources,
                )
            except WeKnoraError as exc:
                raise projection_error_from_provider(exc) from exc
        return self.repository.search(
            owner_user_id=owner_user_id,
            query=normalized,
            maximum_sources=maximum_sources,
        )

    def export_markdown(self, *, owner_user_id: str, source_id: UUID) -> MarkdownExport:
        return self.repository.markdown_export(
            owner_user_id=owner_user_id, source_id=source_id
        )

    def request_delete(
        self, *, owner_user_id: str, source_id: UUID, idempotency_key: str
    ) -> PrivateSourceDeletionView:
        return self.repository.request_delete(
            owner_user_id=owner_user_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
        )

    def deletion(
        self, *, owner_user_id: str, deletion_id: UUID
    ) -> PrivateSourceDeletionView:
        return self.repository.deletion(
            owner_user_id=owner_user_id, deletion_id=deletion_id
        )

    def _read_markdown(self, artifact) -> str:
        if self.markdown_reader is None:
            raise PrivateKnowledgeConflict("private Markdown reader is not configured")
        with self.markdown_reader.open(
            object_ref=str(artifact["object_ref"]),
            expected_sha256=str(artifact["content_sha256"]),
            maximum_bytes=2_000_000,
            prefix="anan-private-markdown-",
            suffix=".md",
        ) as path:
            raw = path.read_bytes()
        try:
            value = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise PrivateKnowledgeConflict("Markdown must be UTF-8 encoded") from exc
        if "\x00" in value:
            raise PrivateKnowledgeConflict("Markdown contains binary data")
        return value


def _split_frontmatter(markdown: str) -> tuple[dict[str, object], str]:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise PrivateKnowledgeConflict("private Markdown is empty")
    if not normalized.startswith("---\n"):
        return {}, normalized
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise PrivateKnowledgeConflict("Markdown frontmatter is not closed")
    try:
        parsed = yaml.safe_load(normalized[4:end]) or {}
    except yaml.YAMLError as exc:
        raise PrivateKnowledgeConflict("Markdown frontmatter is invalid") from exc
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        raise PrivateKnowledgeConflict(
            "Markdown frontmatter must be a string-keyed object"
        )
    body = normalized[end + 5 :].strip()
    if not body:
        raise PrivateKnowledgeConflict("Markdown body is empty")
    return parsed, body


def _validate_round_trip_metadata(metadata: dict[str, object], head) -> None:
    expected = {
        "anan_source_id": str(head["source_id"]),
        "anan_source_version_id": str(head["source_version_id"]),
        "anan_version": int(head["version_no"]),
        "anan_content_sha256": str(head["content_sha256"]),
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise PrivateKnowledgeConflict(
            "Markdown lineage does not match the selected current source version"
        )


def _canonical_markdown(metadata: dict[str, object], body: str) -> str:
    user_metadata = {
        key: value
        for key, value in metadata.items()
        if not key.startswith("anan_") and key not in {"title", "document_kind"}
    }
    if not user_metadata:
        return body.strip()
    header = yaml.safe_dump(
        user_metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{header}\n---\n\n{body.strip()}"


def _markdown_from_recognition(payload: dict[str, object]) -> str:
    raw = payload.get("textSpans")
    if not isinstance(raw, list):
        raise PrivateKnowledgeConflict("confirmed OCR has no text spans")
    by_page: dict[int, list[tuple[float, float, str]]] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("box"), dict):
            continue
        box = item["box"]
        value = str(item.get("text", "")).strip()
        if not value:
            continue
        page = int(box.get("pageNo", 1))
        by_page.setdefault(page, []).append(
            (float(box.get("y", 0)), float(box.get("x", 0)), value)
        )
    if not by_page:
        raise PrivateKnowledgeConflict("confirmed OCR produced no usable text")
    pages = []
    for page_no in sorted(by_page):
        lines = [value for _, _, value in sorted(by_page[page_no])]
        pages.append(f"<!-- anan-page:{page_no} -->\n\n" + "\n\n".join(lines))
    return "\n\n<!-- anan-page-break -->\n\n".join(pages)


def _pages(markdown: str) -> tuple[tuple[int, tuple[str, ...], str], ...]:
    raw_pages = re.split(r"\n\s*<!--\s*anan-page-break\s*-->\s*\n", markdown)
    pages: list[tuple[int, tuple[str, ...], str]] = []
    headings: list[str] = []
    for page_no, raw in enumerate(raw_pages, start=1):
        content = raw.strip()
        if not content:
            continue
        for line in content.splitlines():
            match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
            if match:
                level = len(match.group(1))
                headings = headings[: level - 1] + [match.group(2).strip()]
        pages.append((page_no, tuple(headings), content))
    if not pages:
        raise PrivateKnowledgeConflict("private source has no non-empty pages")
    return tuple(pages)


def _fingerprint(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
