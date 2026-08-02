from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import tarfile
from typing import BinaryIO, Mapping

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from scripts.repair_question_asset_storage import (
    ARCHIVE_ROOT,
    OBJECT_PREFIX,
    AssetReference,
    ObjectMetadata,
    PublicationResult,
    SQLAlchemyQuestionAssetRepository,
    repair_question_assets,
)


class FakeRepository:
    def __init__(self, assets: tuple[AssetReference, ...], store: "FakeStore") -> None:
        self.assets = assets
        self.store = store
        self.updates: dict[str, str] = {}

    def load_assets(self) -> tuple[AssetReference, ...]:
        return self.assets

    def apply_verified_updates(self, updates: Mapping[str, str]) -> PublicationResult:
        assert all(key in self.store.objects for key in updates.values())
        self.updates.update(updates)
        questions = {asset.question_id for asset in self.assets if asset.asset_id in updates}
        return PublicationResult(len(updates), len(questions), 0)


class FakeStore:
    def __init__(self, objects: Mapping[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.puts: list[str] = []
        self.heads: list[str] = []

    def list_objects(self, prefix: str) -> dict[str, ObjectMetadata]:
        return {
            key: ObjectMetadata(exists=True, size=len(payload))
            for key, payload in self.objects.items()
            if key.startswith(prefix)
        }

    def head(self, key: str) -> ObjectMetadata:
        self.heads.append(key)
        payload = self.objects.get(key)
        if payload is None:
            return ObjectMetadata(exists=False)
        return ObjectMetadata(
            exists=True,
            size=len(payload),
            sha256_hex=sha256(payload).hexdigest(),
        )

    def put(
        self,
        *,
        key: str,
        content: BinaryIO,
        size: int,
        sha256_hex: str,
        content_type: str,
    ) -> None:
        payload = content.read()
        assert len(payload) == size
        assert sha256(payload).hexdigest() == sha256_hex
        assert content_type == "image/png"
        assert key not in self.objects
        self.objects[key] = payload
        self.puts.append(key)


def _archive(entries: Mapping[str, bytes]) -> BytesIO:
    result = BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as bundle:
        for name, payload in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            bundle.addfile(info, BytesIO(payload))
    result.seek(0)
    return result


def _asset(
    *,
    asset_id: str = "asset-1",
    question_id: str = "question-1",
    image_src: str = "广东/paper_images/chart.png",
    object_key: str = "data/question-bank-export/files/广东/paper_images/chart.png",
) -> AssetReference:
    return AssetReference(
        asset_id=asset_id,
        question_id=question_id,
        image_src=image_src,
        object_key=object_key,
    )


def _member() -> str:
    return f"{ARCHIVE_ROOT}/广东/paper_images/chart.png"


def _target() -> str:
    return f"{OBJECT_PREFIX}{_member()}"


def test_audit_streams_metadata_and_never_uploads_or_updates() -> None:
    payload = b"png-content"
    store = FakeStore()
    repository = FakeRepository((_asset(),), store)

    report = repair_question_assets(
        mode="audit",
        repository=repository,
        object_store=store,
        archive=_archive({_member(): payload, "unreferenced.png": b"ignored"}),
    )

    assert report.database_assets == 1
    assert report.archive_image_members == 2
    assert report.archive_referenced_members == 1
    assert report.resolved_assets == 1
    assert report.objects_missing == 1
    assert report.objects_uploaded == 0
    assert store.puts == []
    assert repository.updates == {}


def test_apply_uploads_then_updates_and_reports_one_republished_question() -> None:
    store = FakeStore()
    repository = FakeRepository((_asset(),), store)

    report = repair_question_assets(
        mode="apply",
        repository=repository,
        object_store=store,
        archive=_archive({_member(): b"png-content"}),
    )

    assert store.puts == [_target()]
    assert repository.updates == {"asset-1": _target()}
    assert report.objects_uploaded == 1
    assert report.asset_rows_updated == 1
    assert report.questions_republished == 1


def test_apply_reuses_list_verified_object_without_uploading_or_head_calls() -> None:
    payload = b"already-there"
    store = FakeStore({_target(): payload})
    repository = FakeRepository((_asset(),), store)

    report = repair_question_assets(
        mode="apply",
        repository=repository,
        object_store=store,
        archive=_archive({_member(): payload}),
    )

    assert store.puts == []
    assert repository.updates == {"asset-1": _target()}
    assert report.objects_already_present == 1
    assert report.objects_uploaded == 0
    assert store.heads == []


def test_legacy_path_without_province_resolves_unique_archive_suffix() -> None:
    asset = _asset(
        image_src="files/paper_images/chart.png",
        object_key="data/question-bank-export/files/paper_images/chart.png",
    )
    store = FakeStore()
    repository = FakeRepository((asset,), store)

    report = repair_question_assets(
        mode="apply",
        repository=repository,
        object_store=store,
        archive=_archive({_member(): b"unique"}),
    )

    assert report.resolved_assets == 1
    assert report.ambiguous_assets == 0
    assert store.puts == [_target()]
    assert repository.updates == {"asset-1": _target()}


def test_legacy_suffix_in_two_provinces_is_ambiguous_and_uploads_nothing() -> None:
    asset = _asset(
        image_src="files/paper_images/chart.png",
        object_key="data/question-bank-export/files/paper_images/chart.png",
    )
    store = FakeStore()
    repository = FakeRepository((asset,), store)

    report = repair_question_assets(
        mode="apply",
        repository=repository,
        object_store=store,
        archive=_archive(
            {
                _member(): b"guangdong",
                f"{ARCHIVE_ROOT}/广西/paper_images/chart.png": b"guangxi",
            }
        ),
    )

    assert report.ambiguous_assets == 1
    assert report.resolved_assets == 0
    assert store.puts == []
    assert repository.updates == {}


def test_conflicting_database_references_are_not_read_uploaded_or_updated() -> None:
    asset = _asset(
        image_src="广东/paper_images/chart.png",
        object_key="data/question-bank-export/files/广西/other/chart.png",
    )
    store = FakeStore()
    repository = FakeRepository((asset,), store)

    report = repair_question_assets(
        mode="apply",
        repository=repository,
        object_store=store,
        archive=_archive(
            {
                _member(): b"first",
                f"{ARCHIVE_ROOT}/广西/other/chart.png": b"second",
            }
        ),
    )

    assert report.ambiguous_assets == 1
    assert report.archive_referenced_members == 0
    assert store.puts == []
    assert repository.updates == {}


class FailingStore(FakeStore):
    def put(self, **kwargs: object) -> None:
        raise RuntimeError("simulated upload failure")


def test_upload_failure_happens_before_any_database_update() -> None:
    store = FailingStore()
    repository = FakeRepository((_asset(),), store)

    with pytest.raises(RuntimeError, match="simulated upload failure"):
        repair_question_assets(
            mode="apply",
            repository=repository,
            object_store=store,
            archive=_archive({_member(): b"png-content"}),
        )

    assert repository.updates == {}


def test_sql_repository_publishes_new_head_without_mutating_old_snapshot(
    db_session: Session,
) -> None:
    question_id = "question-asset-repair"
    db_session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type,
              stem, answer, material_text, visual_required
            ) values (
              :question_id, 'objective', 'active', '资料分析', 'single',
              '看图选择正确答案', 'A', '', true
            )
            """
        ),
        {"question_id": question_id},
    )
    db_session.execute(
        text(
            """
            insert into product.question_options (
              question_id, option_index, label, option_text
            ) values (:question_id, 1, 'A', '正确')
            """
        ),
        {"question_id": question_id},
    )
    old_key = "data/question-bank-export/files/广东/paper_images/chart.png"
    db_session.execute(
        text(
            """
            insert into product.question_assets (
              asset_id, question_id, part_kind, part_index, image_ordinal,
              image_src, object_key, is_representative_asset
            ) values (
              'asset-repair', :question_id, 'stem_html', 0, 0,
              '广东/paper_images/chart.png', :old_key, true
            )
            """
        ),
        {"question_id": question_id, "old_key": old_key},
    )
    catalog = SQLQuestionContentCatalog(db_session)
    old_version = catalog.publish_current(question_id=question_id).content_version_id
    old_snapshot = db_session.execute(
        text(
            """
            select snapshot_payload
            from product.question_content_versions
            where content_version_id = :version
            """
        ),
        {"version": old_version},
    ).scalar_one()
    assert old_snapshot["assets"][0]["object_key"] == old_key

    store = FakeStore()
    report = repair_question_assets(
        mode="apply",
        repository=SQLAlchemyQuestionAssetRepository(db_session),
        object_store=store,
        archive=_archive({_member(): b"png-content"}),
    )

    new_version = db_session.execute(
        text(
            """
            select content_version_id from product.question_content_heads
            where question_id = :question_id
            """
        ),
        {"question_id": question_id},
    ).scalar_one()
    snapshots = (
        db_session.execute(
            text(
                """
            select content_version_id, snapshot_payload
            from product.question_content_versions
            where question_id = :question_id
            """
            ),
            {"question_id": question_id},
        )
        .mappings()
        .all()
    )

    assert report.questions_republished == 1
    assert new_version != old_version
    assert len(snapshots) == 2
    by_version = {
        str(row["content_version_id"]): dict(row["snapshot_payload"]) for row in snapshots
    }
    assert by_version[old_version]["assets"][0]["object_key"] == old_key
    assert by_version[new_version]["assets"][0]["object_key"] == _target()


def test_sql_repository_updates_unpublished_asset_without_creating_a_head(
    db_session: Session,
) -> None:
    question_id = "question-asset-without-head"
    db_session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type,
              stem, answer, material_text, visual_required
            ) values (
              :question_id, 'objective', 'active', '资料分析', 'single',
              '尚未发布的看图题', 'A', '', true
            )
            """
        ),
        {"question_id": question_id},
    )
    db_session.execute(
        text(
            """
            insert into product.question_assets (
              asset_id, question_id, part_kind, part_index, image_ordinal,
              image_src, object_key, is_representative_asset
            ) values (
              'asset-without-head', :question_id, 'stem_html', 0, 0,
              '广东/paper_images/chart.png', 'legacy/chart.png', true
            )
            """
        ),
        {"question_id": question_id},
    )
    repository = SQLAlchemyQuestionAssetRepository(db_session)
    repository.load_assets()

    result = repository.apply_verified_updates({"asset-without-head": _target()})

    assert result.updated_rows == 1
    assert result.published_questions == 0
    assert result.skipped_questions == 1
    assert (
        db_session.execute(
            text(
                "select count(*) from product.question_content_heads "
                "where question_id = :question_id"
            ),
            {"question_id": question_id},
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            text(
                "select object_key from product.question_assets "
                "where asset_id = 'asset-without-head'"
            )
        ).scalar_one()
        == _target()
    )
