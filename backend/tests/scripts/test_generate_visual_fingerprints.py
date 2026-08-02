from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from scripts.generate_visual_fingerprints import (
    _psycopg_database_url,
    Candidate,
    Fingerprint,
    calculate_fingerprint,
    fetch_candidates,
    process_candidates,
    read_object,
    write_fingerprint,
)
from app.domains.external_ingestion.recognition.image_preprocess import prepare_for_ocr
from app.domains.external_ingestion.recognition.visual_hash import (
    feature_hashes,
    fingerprint_sha256,
)


def test_sqlalchemy_database_url_is_accepted_by_psycopg_script() -> None:
    assert (
        _psycopg_database_url("postgresql+psycopg://user:secret@db/example")
        == "postgresql://user:secret@db/example"
    )
    assert _psycopg_database_url("postgresql:///example") == "postgresql:///example"


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 16), "white").save(output, format="PNG")
    return output.getvalue()


class _MissingObject(Exception):
    status_code = 404


def test_candidates_use_tos_object_key_and_current_content_head() -> None:
    executions: list[tuple[str, list[object]]] = []

    class Cursor:
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, params: list[object]) -> None:
            executions.append((sql, params))

        def fetchall(self) -> list[tuple[object, ...]]:
            return [("q1", "a1", "questions/one.png", "v1", False)]

    conn = SimpleNamespace(cursor=lambda: Cursor())
    result = fetch_candidates(conn, limit=10, shard_count=4, shard_index=2)

    assert result == [Candidate("q1", "a1", "questions/one.png", "v1", False)]
    sql, params = executions[0]
    assert "asset.object_key" in sql
    assert "join product.question_content_heads head" in sql
    assert "asset.image_src" not in sql
    assert "hashtextextended(asset.question_id, 0)" in sql
    assert "limit %s" in sql
    assert params == ["visual-hash-prepared-v2", 4, 2, 10]


def test_fingerprint_uses_the_same_preprocessing_as_live_recognition() -> None:
    source = Image.new("RGB", (480, 480), "#222222")
    source.paste(Image.new("RGB", (360, 360), "white"), (60, 60))
    payload = BytesIO()
    source.save(payload, format="PNG")

    calculated = calculate_fingerprint(payload.getvalue())
    prepared = prepare_for_ocr(source, enable_crop=True, max_long_edge=2200)
    hashes = feature_hashes(prepared.image)

    assert calculated.sha256 == fingerprint_sha256(hashes)
    assert calculated.phash == hashes["phash"]


def test_audit_is_read_only_and_classifies_storage_failures() -> None:
    candidates = [
        Candidate("q1", "a1", "questions/one.png", "v1", False),
        Candidate("q2", "a2", None, "v2", False),
        Candidate("q3", "a3", "questions/missing.png", "v3", False),
        Candidate("q4", "a4", "questions/broken.png", "v4", False),
        Candidate("q5", "a5", "questions/done.png", "v5", True),
    ]

    def read(key: str) -> bytes:
        if key.endswith("missing.png"):
            raise _MissingObject()
        if key.endswith("broken.png"):
            return b"not an image"
        return _png()

    writes: list[tuple[Candidate, Fingerprint]] = []
    stats = process_candidates(
        candidates,
        mode="audit",
        read=read,
        write=lambda candidate, fingerprint: writes.append((candidate, fingerprint)),
    )

    assert writes == []
    assert stats["current_head_assets"] == 5
    assert stats["fingerprint_ready"] == 1
    assert stats["would_write"] == 1
    assert stats["missing_object_key"] == 1
    assert stats["object_missing"] == 1
    assert stats["decode_failed"] == 1
    assert stats["already_complete"] == 1
    assert stats["object_read_failed"] == 0
    assert stats["written"] == 0


def test_apply_writes_only_incomplete_decodable_assets_and_commits() -> None:
    candidate = Candidate("q1", "a1", "questions/one.png", "v1", False)
    writes: list[tuple[Candidate, Fingerprint]] = []
    commits: list[None] = []

    stats = process_candidates(
        [candidate],
        mode="apply",
        read=lambda _key: _png(),
        write=lambda item, fingerprint: writes.append((item, fingerprint)),
        commit=lambda: commits.append(None),
    )

    assert stats["written"] == 1
    assert writes[0][0] == candidate
    assert len(writes[0][1].sha256) == 64
    assert commits == [None]


def test_progress_reports_cumulative_classification_at_configured_interval() -> None:
    candidates = [
        Candidate("q1", "a1", None, "v1", False),
        Candidate("q2", "a2", "questions/done.png", "v2", True),
        Candidate("q3", "a3", "questions/three.png", "v3", False),
    ]
    updates: list[dict[str, int]] = []

    process_candidates(
        candidates,
        mode="audit",
        read=lambda _key: _png(),
        write=lambda _candidate, _fingerprint: None,
        progress_every=2,
        progress=lambda stats: updates.append(dict(stats)),
    )

    assert len(updates) == 1
    assert updates[0]["current_head_assets"] == 2
    assert updates[0]["missing_object_key"] == 1
    assert updates[0]["already_complete"] == 1


def test_read_object_enforces_size_limit_and_closes_response() -> None:
    response = BytesIO(b"12345")
    client = SimpleNamespace(get_object=lambda _bucket, _key: response)

    try:
        read_object(client, bucket="bucket", key="key", maximum_bytes=4)
    except ValueError as exc:
        assert str(exc) == "object_too_large"
    else:
        raise AssertionError("oversized object should fail")
    assert response.closed


def test_write_is_idempotent_upsert_for_the_same_content_fingerprint() -> None:
    executions: list[tuple[str, tuple[object, ...]]] = []

    class Cursor:
        rowcount = 0

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, params: tuple[object, ...]) -> None:
            executions.append((sql, params))

    conn = SimpleNamespace(cursor=lambda: Cursor())
    candidate = Candidate("q1", "a1", "questions/one.png", "v1", False)
    fingerprint = Fingerprint("f" * 64, "1" * 16, "2" * 16, "3" * 16, "4" * 16)

    write_fingerprint(conn, candidate, fingerprint)

    assert "update product.question_visual_fingerprints" in executions[0][0]
    sql, params = executions[1]
    assert "on conflict (content_version_id, fingerprint_sha256) do update" in sql
    assert "is distinct from excluded.phash" in sql
    assert params[1:6] == ("q1", "v1", "f" * 64, "visual-hash-prepared-v2", "a1")
