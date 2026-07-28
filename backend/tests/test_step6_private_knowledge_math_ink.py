from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256, sha512
import hmac
import json
from pathlib import Path
import tempfile
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.math_ink.provider import MyScriptMathClient
from app.domains.math_ink.repository import MathInkNotFound, MathInkRepository
from app.domains.math_ink.schemas import ConfirmFormulaCommand, RecognizeMathInkCommand
from app.domains.math_ink.service import MathInkService
from app.domains.private_knowledge.repository import PrivateKnowledgeRepository
from app.domains.private_knowledge.schemas import CreatePrivateSourceCommand
from app.domains.private_knowledge.service import PrivateKnowledgeService
from app.domains.private_knowledge.worker import PrivateKnowledgeDeletionWorker
from tests.factories import seed_route_frozen_release
from tests.test_planning_routes import _seed_learner_and_published_essay_content
from tests.test_xingce_learning_routes import (
    _client,
    _xingce_task,
)


def test_myscript_request_uses_exact_body_hmac_and_never_sends_hmac_key() -> None:
    captured: dict[str, Any] = {}
    application_key = "application-key"
    hmac_key = "server-hmac-key"

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        captured["body"] = body
        captured["headers"] = dict(request.headers)
        expected = hmac.new(f"{application_key}{hmac_key}".encode(), body, sha512).hexdigest()
        assert request.headers["hmac"] == expected
        assert hmac_key.encode() not in body
        return httpx.Response(
            200,
            headers={"content-type": "application/x-latex"},
            content=b"$\\frac{3}{5}$",
        )

    client = MyScriptMathClient(
        endpoint="https://cloud.myscript.com/api/v4.0/iink/recognize/",
        host_allowlist="cloud.myscript.com",
        application_key=application_key,
        hmac_key=hmac_key,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    command = RecognizeMathInkCommand.model_validate(
        {
            "sessionId": str(uuid4()),
            "questionId": "math-q-1",
            "scaleX": 0.264583,
            "scaleY": 0.264583,
            "strokes": [{"x": [1, 2], "y": [3, 4], "t": [0, 8]}],
        }
    )

    latex, encoded = client.recognize(command)

    assert latex == r"\frac{3}{5}"
    assert encoded == captured["body"]
    assert captured["headers"]["applicationkey"] == application_key


def test_math_ink_candidate_is_idempotent_owner_bound_and_user_confirmed(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "step6-math-owner-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
        include_objective_questions=False,
    )
    seed_route_frozen_release(db_session_factory, count=3)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)
    started = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": "step6-math-session-start"},
        json={"planTaskId": task["id"]},
    )
    assert started.status_code == 200, started.text
    session_view = started.json()["data"]
    question_id = session_view["bundle"]["questions"][0]["questionId"]
    command = RecognizeMathInkCommand.model_validate(
        {
            "sessionId": session_view["sessionId"],
            "questionId": question_id,
            "sessionAtMs": 12_000,
            "questionElapsedMs": 8_000,
            "scaleX": 0.264583,
            "scaleY": 0.264583,
            "strokes": [{"x": [10, 20], "y": [30, 40], "t": [0, 12]}],
        }
    )
    provider = _MathProvider()
    settings = Settings(
        myscript_provider_release_id="myscript-iink-cloud-v8",
        myscript_model_release="iink-math-v4.4",
        myscript_price_version="myscript-price-test-v1",
        myscript_unit_price_cny="0.01",
    )

    with db_session_factory() as session:
        service = MathInkService(MathInkRepository(session, settings), provider)  # type: ignore[arg-type]
        candidate = service.recognize(
            owner_user_id=user_id,
            command=command,
            idempotency_key="step6-math-recognize",
        )
        replay = service.recognize(
            owner_user_id=user_id,
            command=command,
            idempotency_key="step6-math-recognize",
        )
        assert candidate.formula is not None
        assert replay.formula == candidate.formula
        assert provider.calls == 1
        stored_clock = session.execute(
            text(
                "select ink_payload from learning.math_ink_commands "
                "where request_id = cast(:request_id as uuid)"
            ),
            {"request_id": str(candidate.request_id)},
        ).scalar_one()
        assert stored_clock["sessionAtMs"] == 12_000
        assert stored_clock["questionElapsedMs"] == 8_000
        confirmed = service.confirm(
            owner_user_id=user_id,
            formula_version_id=candidate.formula.formula_version_id,
            command=ConfirmFormulaCommand(expectedVersionNo=1, latex=r"x=\frac{3}{5}"),
        )
        session.commit()
        assert confirmed.state == "confirmed"
        assert confirmed.version_no == 2
        assert confirmed.latex == r"x=\frac{3}{5}"
        assert (
            session.execute(
                text("select count(*) from ai_audit.external_ai_operation_ledger")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("select count(*) from ai_audit.external_processing_receipts")
            ).scalar_one()
            == 1
        )

    other_user = _seed_user(db_session_factory)
    with db_session_factory() as session:
        service = MathInkService(MathInkRepository(session, settings), None)
        try:
            service.confirm(
                owner_user_id=other_user,
                formula_version_id=candidate.formula.formula_version_id,
                command=ConfirmFormulaCommand(expectedVersionNo=1, latex=r"x=\frac{3}{5}"),
            )
        except MathInkNotFound:
            pass
        else:
            raise AssertionError("another account confirmed the owner's formula")


def test_math_ink_active_clock_must_be_in_range_and_in_order() -> None:
    payload = {
        "sessionId": str(uuid4()),
        "questionId": "math-clock-q",
        "sessionAtMs": 8_000,
        "questionElapsedMs": 8_001,
        "scaleX": 0.264583,
        "scaleY": 0.264583,
        "strokes": [{"x": [1, 2], "y": [3, 4], "t": [0, 8]}],
    }

    try:
        RecognizeMathInkCommand.model_validate(payload)
    except ValueError as exc:
        assert "question elapsed time cannot exceed" in str(exc)
    else:
        raise AssertionError("question clock exceeded the session clock")


def test_private_markdown_round_trip_search_isolation_and_complete_delete(
    db_session_factory: sessionmaker[Session],
) -> None:
    owner = _seed_user(db_session_factory)
    other = _seed_user(db_session_factory)
    first_markdown = """---
tags:
  - 资料分析
aliases: [基期量]
---

# 资料分析

基期量等于现期量除以一加增长率。

<!-- anan-page-break -->

# 易错点

百分数增长率要先换成小数。
"""
    first_artifact, first_ref = _insert_markdown_artifact(
        db_session_factory, owner=owner, content=first_markdown
    )
    reader = _MarkdownReader({first_ref: first_markdown})

    with db_session_factory() as session:
        service = PrivateKnowledgeService(
            PrivateKnowledgeRepository(session), markdown_reader=reader
        )
        first = service.create(
            owner_user_id=owner,
            command=CreatePrivateSourceCommand(
                artifactVersionId=first_artifact,
                recognitionRevisionId=None,
                title="资料分析高频公式",
                documentKind="note",
            ),
            idempotency_key="step6-private-create",
        )
        assert first.page_count == 2
        assert first.chunk_count >= 2
        assert service.search(owner_user_id=owner, query="基期量").citations
        assert not service.search(owner_user_id=other, query="基期量").citations
        exported = service.export_markdown(owner_user_id=owner, source_id=first.source_id)
        assert exported.content.count("anan_source_id:") == 1
        assert "tags:" in exported.content
        session.commit()

    second_markdown = exported.content.replace(
        "百分数增长率要先换成小数。",
        "百分数增长率要先换成小数，并核对单位。",
    )
    second_artifact, second_ref = _insert_markdown_artifact(
        db_session_factory, owner=owner, content=second_markdown
    )
    reader.values[second_ref] = second_markdown
    with db_session_factory() as session:
        service = PrivateKnowledgeService(
            PrivateKnowledgeRepository(session), markdown_reader=reader
        )
        second = service.create(
            owner_user_id=owner,
            command=CreatePrivateSourceCommand(
                artifactVersionId=second_artifact,
                recognitionRevisionId=None,
                title="资料分析高频公式",
                documentKind="note",
                sourceId=first.source_id,
                expectedVersionNo=1,
            ),
            idempotency_key="step6-private-import-v2",
        )
        assert second.version_no == 2
        assert service.list(owner_user_id=owner).sources == (second,)
        citation = service.search(owner_user_id=owner, query="核对单位").citations[0]
        assert citation.source_version_id == second.source_version_id
        deletion = service.request_delete(
            owner_user_id=owner,
            source_id=first.source_id,
            idempotency_key="step6-private-delete",
        )
        assert deletion.source_version_count == 2
        session.commit()

    storage = _DeleteStorage()
    with db_session_factory() as session:
        processed = PrivateKnowledgeDeletionWorker(storage=storage).process_next(session)  # type: ignore[arg-type]
        assert processed is not None
        assert processed.status == "succeeded"

    assert set(storage.deleted) == {first_ref, second_ref}
    with db_session_factory() as session:
        for table in (
            "learning.private_knowledge_source_versions",
            "learning.private_knowledge_page_versions",
            "learning.private_knowledge_chunks",
            "learning.private_knowledge_search_index",
        ):
            assert session.execute(text(f"select count(*) from {table}")).scalar_one() == 0
        states = (
            session.execute(
                text(
                    "select state from learning.source_artifact_versions "
                    "where artifact_version_id = any(cast(:ids as uuid[])) order by state"
                ),
                {"ids": [str(first_artifact), str(second_artifact)]},
            )
            .scalars()
            .all()
        )
        assert states == ["deleted", "deleted"]
        assert (
            session.execute(
                text(
                    "select state from privacy.private_material_deletion_requests "
                    "where deletion_request_id = cast(:id as uuid)"
                ),
                {"id": str(deletion.deletion_request_id)},
            ).scalar_one()
            == "completed"
        )


class _MathProvider:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, command: RecognizeMathInkCommand) -> tuple[str, bytes]:
        self.calls += 1
        return r"\frac{3}{5}", json.dumps(command.model_dump(mode="json")).encode()


class _MarkdownReader:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    @contextmanager
    def open(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        maximum_bytes: int,
        prefix: str,
        suffix: str = "",
    ) -> Iterator[Path]:
        value = self.values[object_ref].encode()
        assert sha256(value).hexdigest() == expected_sha256
        assert len(value) <= maximum_bytes
        with tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix) as handle:
            handle.write(value)
            handle.flush()
            yield Path(handle.name)


class _DeleteStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        assert operation_key
        self.deleted.append(object_ref)


def _seed_user(factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:id as uuid), '步骤六学习者')"
            ),
            {"id": user_id},
        )
    return user_id


def _insert_markdown_artifact(
    factory: sessionmaker[Session], *, owner: str, content: str
) -> tuple[UUID, str]:
    artifact_version_id = uuid4()
    artifact_id = uuid4()
    object_ref = f"tos://step6/private/{artifact_version_id}.md"
    encoded = content.encode()
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.source_artifact_versions (
                  artifact_version_id, artifact_id, owner_user_id, version_no,
                  source_kind, state, object_ref, mime_type, content_sha256,
                  byte_size, page_count, supersedes_artifact_version_id,
                  contract_payload, created_at
                ) values (
                  cast(:version_id as uuid), cast(:artifact_id as uuid),
                  cast(:owner as uuid), 1, 'notes_app_export', 'available',
                  :object_ref, 'text/markdown', :digest, :byte_size, null, null,
                  '{"contractVersion":"private-markdown-test@v1"}'::jsonb, :created_at
                )
                """
            ),
            {
                "version_id": str(artifact_version_id),
                "artifact_id": str(artifact_id),
                "owner": owner,
                "object_ref": object_ref,
                "digest": sha256(encoded).hexdigest(),
                "byte_size": len(encoded),
                "created_at": datetime.now(UTC),
            },
        )
    return artifact_version_id, object_ref
