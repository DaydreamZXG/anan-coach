from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.domains.ai.governance.contracts import GlobalAskContext
from app.domains.ai.governance.retrieval import AuthorizedHybridRetriever
from app.domains.data_rights.repository import (
    CONFIRMATION_PHRASE,
    DataRightsRepository,
)
from app.domains.private_knowledge.projection import (
    PrivateKnowledgeProjectionError,
    PrivateKnowledgeProjectionGateway,
)
from app.domains.private_knowledge.repository import PrivateKnowledgeRepository
from app.domains.private_knowledge.schemas import CreatePrivateSourceCommand
from app.domains.private_knowledge.service import PrivateKnowledgeService
from app.domains.private_knowledge.weknora import (
    WeKnoraCandidate,
    WeKnoraClient,
    WeKnoraError,
    WeKnoraKnowledge,
    WeKnoraNotFound,
    WeKnoraSettings,
)
from app.domains.private_knowledge.worker import PrivateKnowledgeDeletionWorker
from app.domains.private_knowledge.weknora_upstream import (
    UPSTREAM_COMMIT,
    UPSTREAM_TARBALL_SHA256,
    UPSTREAM_TREE,
    UPSTREAM_VERSION,
)
from tests.test_step6_private_knowledge_math_ink import (
    _DeleteStorage,
    _MarkdownReader,
    _insert_markdown_artifact,
    _seed_user,
)


def test_weknora_transport_rejects_redirect_and_never_serializes_api_key() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(307, headers={"location": "https://evil.example/api"})

    settings = _settings()
    client = WeKnoraClient(
        settings,
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    with pytest.raises(WeKnoraError, match="weknora_redirect_rejected"):
        client.hybrid_search(
            knowledge_base_id="kb-1",
            query="基期量",
            knowledge_ids=("knowledge-1",),
            match_count=8,
        )

    assert captured["headers"]["x-api-key"] == "server-only-retrieve-key"
    assert b"server-only-retrieve-key" not in captured["body"]


def test_weknora_runtime_matches_reviewed_upstream_lock() -> None:
    lock = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "ops"
            / "upstreams"
            / "tencent-weknora-0.7.0.json"
        ).read_text(encoding="utf-8")
    )

    assert lock["version"] == UPSTREAM_VERSION
    assert lock["commit"] == UPSTREAM_COMMIT
    assert lock["tree"] == UPSTREAM_TREE
    assert lock["tarballSha256"] == UPSTREAM_TARBALL_SHA256
    assert lock["requiredCapabilities"] == ["manage_kbs", "ingest", "retrieve"]


def test_required_database_revision_tracks_projection_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REQUIRED_DATABASE_REVISION", raising=False)

    assert Settings().required_database_revision == "20260721_51"
    assert get_settings().required_database_revision == "20260721_51"


def test_operations_env_declares_weknora_activation_contract() -> None:
    values = _read_env_example(
        Path(__file__).resolve().parents[2] / "ops" / "env.example"
    )

    assert values["REQUIRED_DATABASE_REVISION"] == "20260721_51"
    assert values["WEKNORA_PRIVATE_KNOWLEDGE_MODE"] == "canonical"
    assert values["WEKNORA_BASE_URL"] == ""
    assert values["WEKNORA_MANAGE_API_KEY"] == ""
    assert values["WEKNORA_INGEST_API_KEY"] == ""
    assert values["WEKNORA_RETRIEVE_API_KEY"] == ""
    assert values["WEKNORA_RELEASE_ID"] == ""
    assert values["WEKNORA_HTTPS_HOST_ALLOWLIST"] == ""
    assert values["WEKNORA_REQUEST_TIMEOUT_SECONDS"] == "30"
    assert values["WEKNORA_SEARCH_BATCH_SIZE"] == "500"


def test_weknora_knowledge_listing_exhausts_all_pages() -> None:
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_pages.append(request.url.query.decode("ascii"))
        page = int(request.url.params["page"])
        item = {
            "id": f"knowledge-{page}",
            "title": f"anan:chunk-{page}",
            "parse_status": "completed",
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [item],
                "page": page,
                "page_size": 200,
                "total": 2,
            },
        )

    client = WeKnoraClient(
        _settings(),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    values = client.list_knowledge("kb-owner-private")

    assert [item.knowledge_id for item in values] == ["knowledge-1", "knowledge-2"]
    assert requested_pages == ["page=1&page_size=200", "page=2&page_size=200"]


def test_projection_indexes_exact_chunks_searches_canonical_rows_and_deletes_remote_first(
    db_session_factory: sessionmaker[Session],
) -> None:
    _apply_projection_schema(db_session_factory)
    owner = _seed_user(db_session_factory)
    other = _seed_user(db_session_factory)
    markdown = """# 资料分析

基期量等于现期量除以一加增长率。

<!-- anan-page-break -->

# 易错点

百分数增长率要先换成小数。
"""
    artifact_id, object_ref = _insert_markdown_artifact(
        db_session_factory, owner=owner, content=markdown
    )
    provider = _FakeWeKnora()
    storage = _DeleteStorage()

    with db_session_factory() as session:
        gateway = _gateway(session, provider)
        service = PrivateKnowledgeService(
            PrivateKnowledgeRepository(session),
            markdown_reader=_MarkdownReader({object_ref: markdown}),
            projection_gateway=gateway,
        )
        source = service.create(
            owner_user_id=owner,
            command=CreatePrivateSourceCommand(
                artifactVersionId=artifact_id,
                title="资料分析公式",
                documentKind="note",
            ),
            idempotency_key="weknora-source-create",
        )
        session.commit()

        worker = PrivateKnowledgeDeletionWorker(
            storage=storage, projection_gateway=gateway
        )
        indexed = worker.process_next(session)
        assert indexed is not None and indexed.status == "succeeded"
        projection_rows = session.execute(
            text(
                "select remote_knowledge_id, chunk_id, state "
                "from learning.private_knowledge_chunk_projections "
                "where owner_user_id = cast(:owner as uuid) order by deterministic_title"
            ),
            {"owner": owner},
        ).mappings().all()
        assert len(projection_rows) == source.chunk_count
        assert {row["state"] for row in projection_rows} == {"ready"}
        provider.search_order = [str(projection_rows[-1]["remote_knowledge_id"])]

        result = service.search(
            owner_user_id=owner, query="增长率", maximum_sources=8
        )
        assert [item.chunk_id for item in result.citations] == [
            projection_rows[-1]["chunk_id"]
        ]
        canonical_content = session.execute(
            text(
                "select content from learning.private_knowledge_chunks "
                "where chunk_id = cast(:chunk_id as uuid)"
            ),
            {"chunk_id": str(projection_rows[-1]["chunk_id"])},
        ).scalar_one()
        assert result.citations[0].excerpt == " ".join(canonical_content.split())[:2000]

        other_service = PrivateKnowledgeService(
            PrivateKnowledgeRepository(session), projection_gateway=gateway
        )
        assert not other_service.search(
            owner_user_id=other, query="增长率", maximum_sources=8
        ).citations

        deletion = service.request_delete(
            owner_user_id=owner,
            source_id=source.source_id,
            idempotency_key="weknora-source-delete",
        )
        session.commit()
        deleted = worker.process_next(session)
        assert deleted is not None and deleted.status == "succeeded"
        assert deleted.result is not None
        assert deleted.result["remoteProjectionsDeleted"] == source.chunk_count
        assert service.deletion(
            owner_user_id=owner,
            deletion_id=deletion.deletion_request_id,
        ).state == "completed"

    assert storage.deleted == [object_ref]
    assert provider.knowledge == {}


def test_uncertain_manual_create_is_reconciled_by_deterministic_title_without_duplicate(
    db_session_factory: sessionmaker[Session],
) -> None:
    _apply_projection_schema(db_session_factory)
    owner = _seed_user(db_session_factory)
    markdown = "# 判断推理\n\n削弱题先找论点与论据。"
    artifact_id, object_ref = _insert_markdown_artifact(
        db_session_factory, owner=owner, content=markdown
    )
    provider = _FakeWeKnora(uncertain_first_create=True)

    with db_session_factory() as session:
        gateway = _gateway(session, provider)
        service = PrivateKnowledgeService(
            PrivateKnowledgeRepository(session),
            markdown_reader=_MarkdownReader({object_ref: markdown}),
            projection_gateway=gateway,
        )
        service.create(
            owner_user_id=owner,
            command=CreatePrivateSourceCommand(
                artifactVersionId=artifact_id,
                title="判断推理笔记",
                documentKind="note",
            ),
            idempotency_key="weknora-uncertain-create",
        )
        session.commit()
        worker = PrivateKnowledgeDeletionWorker(
            storage=_DeleteStorage(), projection_gateway=gateway
        )

        first = worker.process_next(session)
        assert first is not None and first.status == "queued"
        session.execute(
            text(
                "update platform.background_jobs set run_after = :now "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(first.job_id), "now": datetime.now(UTC)},
        )
        session.commit()
        second = worker.process_next(session)
        assert second is not None and second.status == "succeeded"

    assert provider.create_calls == 1
    assert len(provider.knowledge) == 1


def test_active_projection_search_fails_closed_on_provider_error(
    db_session_factory: sessionmaker[Session],
) -> None:
    _apply_projection_schema(db_session_factory)
    owner = _seed_user(db_session_factory)
    markdown = "# 言语理解\n\n主旨题要找文段主题句。"
    artifact_id, object_ref = _insert_markdown_artifact(
        db_session_factory, owner=owner, content=markdown
    )
    provider = _FakeWeKnora()

    with db_session_factory() as session:
        gateway = _gateway(session, provider)
        service = PrivateKnowledgeService(
            PrivateKnowledgeRepository(session),
            markdown_reader=_MarkdownReader({object_ref: markdown}),
            projection_gateway=gateway,
        )
        service.create(
            owner_user_id=owner,
            command=CreatePrivateSourceCommand(
                artifactVersionId=artifact_id,
                title="言语理解笔记",
                documentKind="note",
            ),
            idempotency_key="weknora-fail-closed",
        )
        session.commit()
        worker = PrivateKnowledgeDeletionWorker(
            storage=_DeleteStorage(), projection_gateway=gateway
        )
        assert worker.process_next(session).status == "succeeded"  # type: ignore[union-attr]
        provider.search_error = WeKnoraError(
            "weknora_network_unavailable", retryable=True
        )

        with pytest.raises(
            PrivateKnowledgeProjectionError, match="weknora_network_unavailable"
        ):
            service.search(owner_user_id=owner, query="主旨", maximum_sources=8)


def test_governed_ai_retrieval_uses_remote_candidates_but_canonical_content(
    db_session_factory: sessionmaker[Session],
) -> None:
    _apply_projection_schema(db_session_factory)
    owner = _seed_user(db_session_factory)
    markdown = "# 数量关系\n\n浓度问题先守住溶质量。"
    artifact_id, object_ref = _insert_markdown_artifact(
        db_session_factory, owner=owner, content=markdown
    )
    provider = _FakeWeKnora()

    with db_session_factory() as session:
        gateway = _gateway(session, provider)
        service = PrivateKnowledgeService(
            PrivateKnowledgeRepository(session),
            markdown_reader=_MarkdownReader({object_ref: markdown}),
            projection_gateway=gateway,
        )
        source = service.create(
            owner_user_id=owner,
            command=CreatePrivateSourceCommand(
                artifactVersionId=artifact_id,
                title="浓度问题笔记",
                documentKind="note",
            ),
            idempotency_key="weknora-ai-retrieval-source",
        )
        session.commit()
        worker = PrivateKnowledgeDeletionWorker(
            storage=_DeleteStorage(), projection_gateway=gateway
        )
        assert worker.process_next(session).status == "succeeded"  # type: ignore[union-attr]
        remote_id = session.execute(
            text(
                "select remote_knowledge_id "
                "from learning.private_knowledge_chunk_projections "
                "where owner_user_id = cast(:owner as uuid) and state = 'ready'"
            ),
            {"owner": owner},
        ).scalar_one()
        provider.search_order = [str(remote_id)]

        bundle = AuthorizedHybridRetriever(
            session,
            private_projection=gateway,
            resolve_projection_from_environment=False,
        ).retrieve(
            user_id=owner,
            question="浓度问题怎么做",
            context=GlobalAskContext(
                routeId="pages/companion/index",
                workspace="training",
                objectRefs=[],
                learnerStateVersion="brain-test-v1",
            ),
        )

        assert len(bundle.sources) == 1
        retrieved = bundle.sources[0]
        assert retrieved.reference.source_id == f"private-source:{source.source_id}"
        assert retrieved.reference.version == str(source.source_version_id)
        assert retrieved.excerpt == " ".join(markdown.split())
        assert remote_id not in retrieved.model_dump_json()


def test_account_erasure_cleanup_removes_dedicated_remote_space_before_canonical_delete(
    db_session_factory: sessionmaker[Session],
) -> None:
    _apply_projection_schema(db_session_factory)
    owner = _seed_user(db_session_factory)
    markdown = "# 常识判断\n\n私人笔记只属于当前账号。"
    artifact_id, object_ref = _insert_markdown_artifact(
        db_session_factory, owner=owner, content=markdown
    )
    provider = _FakeWeKnora()

    with db_session_factory() as session:
        gateway = _gateway(session, provider)
        service = PrivateKnowledgeService(
            PrivateKnowledgeRepository(session),
            markdown_reader=_MarkdownReader({object_ref: markdown}),
            projection_gateway=gateway,
        )
        source = service.create(
            owner_user_id=owner,
            command=CreatePrivateSourceCommand(
                artifactVersionId=artifact_id,
                title="常识判断笔记",
                documentKind="note",
            ),
            idempotency_key="weknora-account-delete-source",
        )
        session.commit()
        worker = PrivateKnowledgeDeletionWorker(
            storage=_DeleteStorage(), projection_gateway=gateway
        )
        assert worker.process_next(session).status == "succeeded"  # type: ignore[union-attr]
        DataRightsRepository(session).create_deletion(
            user_id=owner,
            command_key="weknora-account-delete",
            confirmation_phrase=CONFIRMATION_PHRASE,
            now=datetime.now(UTC),
        )
        session.commit()

        provider.space_delete_error = WeKnoraError(
            "weknora_network_unavailable", uncertain=True
        )
        with pytest.raises(WeKnoraError, match="weknora_network_unavailable"):
            gateway.cleanup_account_deletions()
        assert session.execute(
            text(
                "select bool_and(state = 'ready') "
                "from learning.private_knowledge_chunk_projections "
                "where owner_user_id = cast(:owner as uuid)"
            ),
            {"owner": owner},
        ).scalar_one() is True
        provider.space_delete_error = None
        assert gateway.cleanup_account_deletions() == 1
        assert not hasattr(provider, "knowledge_base_name")
        assert not provider.knowledge
        assert session.execute(
            text(
                "select state from learning.private_knowledge_projection_spaces "
                "where owner_user_id = cast(:owner as uuid)"
            ),
            {"owner": owner},
        ).scalar_one() == "deleted"
        assert session.execute(
            text(
                "select bool_and(state = 'deleted') "
                "from learning.private_knowledge_chunk_projections "
                "where owner_user_id = cast(:owner as uuid)"
            ),
            {"owner": owner},
        ).scalar_one() is True
        assert service.list(owner_user_id=owner).sources[0].source_id == source.source_id


class _FakeWeKnora:
    def __init__(self, *, uncertain_first_create: bool = False) -> None:
        self.knowledge_base_id = "kb-owner-private"
        self.knowledge: dict[str, WeKnoraKnowledge] = {}
        self.search_order: list[str] = []
        self.search_error: WeKnoraError | None = None
        self.space_delete_error: WeKnoraError | None = None
        self.uncertain_first_create = uncertain_first_create
        self.create_calls = 0
        self.events: list[str] = []

    def list_knowledge_bases(self) -> tuple[dict[str, str], ...]:
        if not hasattr(self, "knowledge_base_name"):
            return ()
        return ({"id": self.knowledge_base_id, "name": self.knowledge_base_name},)

    def create_knowledge_base(self, *, name: str) -> str:
        self.knowledge_base_name = name
        return self.knowledge_base_id

    def get_knowledge_base(self, knowledge_base_id: str) -> dict[str, Any]:
        if not hasattr(self, "knowledge_base_name"):
            raise WeKnoraNotFound("weknora_not_found")
        return {"id": knowledge_base_id, "name": self.knowledge_base_name}

    def delete_knowledge_base(self, knowledge_base_id: str) -> None:
        if self.space_delete_error is not None:
            raise self.space_delete_error
        del self.knowledge_base_name

    def list_knowledge(self, knowledge_base_id: str) -> tuple[WeKnoraKnowledge, ...]:
        return tuple(self.knowledge.values())

    def create_manual_knowledge(
        self, *, knowledge_base_id: str, title: str, content: str
    ) -> WeKnoraKnowledge:
        self.create_calls += 1
        item = WeKnoraKnowledge(
            knowledge_id=f"knowledge-{self.create_calls}",
            title=title,
            parse_status="completed",
        )
        self.knowledge[item.knowledge_id] = item
        if self.uncertain_first_create and self.create_calls == 1:
            raise WeKnoraError(
                "weknora_network_unavailable", uncertain=True
            )
        return item

    def get_knowledge(self, knowledge_id: str) -> WeKnoraKnowledge:
        try:
            return self.knowledge[knowledge_id]
        except KeyError as exc:
            raise WeKnoraNotFound("weknora_not_found") from exc

    def delete_knowledge(self, knowledge_id: str) -> None:
        self.events.append("delete-knowledge")
        self.knowledge.pop(knowledge_id, None)

    def hybrid_search(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        knowledge_ids: tuple[str, ...],
        match_count: int,
    ) -> tuple[WeKnoraCandidate, ...]:
        if self.search_error is not None:
            raise self.search_error
        selected = self.search_order or list(knowledge_ids[:match_count])
        return tuple(
            WeKnoraCandidate(knowledge_id=value, score=0.9 - index * 0.01)
            for index, value in enumerate(selected)
        )


def _settings() -> WeKnoraSettings:
    return WeKnoraSettings(
        mode="weknora",
        base_url="https://weknora.internal",
        manage_api_key="server-only-manage-key",
        ingest_api_key="server-only-ingest-key",
        retrieve_api_key="server-only-retrieve-key",
        release_id="weknora-test-release",
        https_host_allowlist=("weknora.internal",),
        request_timeout_seconds=10.0,
        search_batch_size=500,
    )


def _gateway(
    session: Session, provider: _FakeWeKnora
) -> PrivateKnowledgeProjectionGateway:
    return PrivateKnowledgeProjectionGateway(
        session,
        client=provider,  # type: ignore[arg-type]
        settings=_settings(),
    )


def _apply_projection_schema(factory: sessionmaker[Session]) -> None:
    projection_migration = import_module(
        "migrations.versions.20260721_50_private_knowledge_weknora"
    )
    with factory.begin() as session:
        for statement in projection_migration._TEST_STATEMENTS:
            session.execute(text(statement))


def _read_env_example(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise AssertionError(f"invalid or duplicate environment line: {raw_line}")
        values[key] = value
    return values
