#!/usr/bin/env python3
"""Audit or repair Fenbi question-image objects and immutable content heads.

The archive is consumed sequentially (including ``--archive -``); it is never
expanded as a tree.  Only regular image members that exactly resolve a path
referenced by ``product.question_assets`` are read or uploaded.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import mimetypes
import os
from pathlib import PurePosixPath
import sys
import tarfile
from tempfile import TemporaryDirectory
from typing import BinaryIO, Protocol
from urllib.parse import unquote, urlsplit

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog


ARCHIVE_ROOT = "fenbi-final-analysis-v1-20260613-assets"
OBJECT_PREFIX = "data/question-bank-export/files/source-question-bank/"
IMAGE_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})


@dataclass(frozen=True)
class AssetReference:
    asset_id: str
    question_id: str
    image_src: str
    object_key: str


@dataclass(frozen=True)
class ObjectMetadata:
    exists: bool
    size: int | None = None
    sha256_hex: str | None = None


@dataclass(frozen=True)
class PublicationResult:
    updated_rows: int
    published_questions: int
    skipped_questions: int
    failed_question_ids: tuple[str, ...] = ()


@dataclass
class RepairReport:
    mode: str
    database_assets: int = 0
    archive_image_members: int = 0
    archive_referenced_members: int = 0
    tos_listed_objects: int = 0
    resolved_assets: int = 0
    unresolved_assets: int = 0
    ambiguous_assets: int = 0
    objects_already_present: int = 0
    objects_missing: int = 0
    objects_uploaded: int = 0
    objects_failed_verification: int = 0
    asset_rows_updated: int = 0
    questions_republished: int = 0
    questions_not_republished: int = 0
    publication_failures: int = 0
    publication_failure_samples: tuple[str, ...] = ()
    unresolved_asset_samples: tuple[str, ...] = ()
    ambiguous_asset_samples: tuple[str, ...] = ()


class QuestionAssetRepository(Protocol):
    def load_assets(self) -> tuple[AssetReference, ...]: ...

    def apply_verified_updates(self, updates: Mapping[str, str]) -> PublicationResult: ...


class ObjectStore(Protocol):
    def list_objects(self, prefix: str) -> dict[str, ObjectMetadata]: ...

    def head(self, key: str) -> ObjectMetadata: ...

    def put(
        self,
        *,
        key: str,
        content: BinaryIO,
        size: int,
        sha256_hex: str,
        content_type: str,
    ) -> None: ...


class SQLAlchemyQuestionAssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._assets_by_id: dict[str, AssetReference] = {}

    def load_assets(self) -> tuple[AssetReference, ...]:
        rows = self._session.execute(
            text(
                """
                select asset_id, question_id,
                       coalesce(image_src, '') as image_src,
                       coalesce(object_key, '') as object_key
                from product.question_assets
                where nullif(trim(image_src), '') is not null
                   or nullif(trim(object_key), '') is not null
                order by asset_id
                """
            )
        ).mappings()
        assets = tuple(
            AssetReference(
                asset_id=str(row["asset_id"]),
                question_id=str(row["question_id"]),
                image_src=str(row["image_src"]),
                object_key=str(row["object_key"]),
            )
            for row in rows
        )
        self._assets_by_id = {asset.asset_id: asset for asset in assets}
        return assets

    def apply_verified_updates(self, updates: Mapping[str, str]) -> PublicationResult:
        if not updates:
            return PublicationResult(0, 0, 0)
        by_question: dict[str, list[dict[str, str]]] = defaultdict(list)
        for asset_id, object_key in sorted(updates.items()):
            asset = self._assets_by_id.get(asset_id)
            if asset is None:
                raise RuntimeError(f"asset {asset_id!r} was not loaded by this repository")
            by_question[asset.question_id].append({"asset_id": asset_id, "object_key": object_key})
        question_ids = tuple(by_question)
        eligible = {
            str(value)
            for value in self._session.execute(
                text(
                    """
                    select question.question_id
                    from product.questions question
                    join product.question_content_heads head
                      on head.question_id = question.question_id
                    where question.question_id = any(cast(:question_ids as text[]))
                      and question.status in ('active', 'canonical')
                    """
                ),
                {"question_ids": list(question_ids)},
            ).scalars()
        }
        catalog = SQLQuestionContentCatalog(self._session)
        statement = text(
            """
            update product.question_assets as asset
            set object_key = candidate.object_key
            from jsonb_to_recordset(cast(:updates as jsonb))
                 as candidate(asset_id text, object_key text)
            where asset.asset_id = candidate.asset_id
              and asset.object_key is distinct from candidate.object_key
            """
        )
        updated_rows = 0
        published = 0
        skipped = 0
        failed: list[str] = []
        for question_id, rows in sorted(by_question.items()):
            try:
                with self._session.begin_nested():
                    result = self._session.execute(
                        statement,
                        {"updates": json.dumps(rows, ensure_ascii=False)},
                    )
                    changed = int(result.rowcount or 0)
                    if changed and question_id in eligible:
                        catalog.publish_current(question_id=question_id)
            except Exception as exc:
                failed.append(question_id)
                print(
                    f"question publication failed for {question_id}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            updated_rows += changed
            if not changed:
                continue
            if question_id in eligible:
                published += 1
            else:
                skipped += 1
        self._session.commit()
        return PublicationResult(updated_rows, published, skipped, tuple(failed))


class TosObjectStore:
    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        endpoint: str,
        region: str,
        bucket: str,
    ) -> None:
        try:
            import tos
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("the official tos package is required") from exc
        if not all((access_key, secret_key, endpoint, region, bucket)):
            raise ValueError("TOS configuration is incomplete")
        self._tos = tos
        self._client = tos.TosClientV2(access_key, secret_key, endpoint, region)
        self._bucket = bucket

    def list_objects(self, prefix: str) -> dict[str, ObjectMetadata]:
        objects: dict[str, ObjectMetadata] = {}
        token: str | None = None
        while True:
            response = self._client.list_objects_type2(
                self._bucket,
                prefix=prefix,
                continuation_token=token,
                max_keys=1_000,
            )
            for item in getattr(response, "contents", None) or ():
                size = getattr(item, "size", None)
                objects[str(item.key)] = ObjectMetadata(
                    exists=True,
                    size=int(size) if size is not None else None,
                )
            if not bool(getattr(response, "is_truncated", False)):
                return objects
            token = str(getattr(response, "next_continuation_token", "") or "")
            if not token:
                raise RuntimeError("TOS returned a truncated listing without a token")

    def head(self, key: str) -> ObjectMetadata:
        try:
            response = self._client.head_object(self._bucket, key)
        except Exception as exc:
            if _tos_status(exc) == 404:
                return ObjectMetadata(exists=False)
            raise
        metadata = getattr(response, "meta", None) or {}
        digest = metadata.get("content-sha256") or metadata.get("sha256")
        size = getattr(response, "content_length", None)
        return ObjectMetadata(
            exists=True,
            size=int(size) if size is not None else None,
            sha256_hex=str(digest) if digest else None,
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
        content.seek(0)
        self._client.put_object(
            self._bucket,
            key,
            content=content,
            content_length=size,
            content_sha256=sha256_hex,
            content_type=content_type,
            acl=self._tos.ACLType.ACL_Public_Read,
            meta={"content-sha256": sha256_hex},
            forbid_overwrite=True,
        )


def repair_question_assets(
    *,
    mode: str,
    repository: QuestionAssetRepository,
    object_store: ObjectStore,
    archive: BinaryIO,
    object_prefix: str = OBJECT_PREFIX,
) -> RepairReport:
    if mode not in {"audit", "apply"}:
        raise ValueError("mode must be audit or apply")
    report = RepairReport(mode=mode)
    assets = repository.load_assets()
    report.database_assets = len(assets)
    aliases, conflicting_assets = _asset_alias_index(assets)
    inventory = object_store.list_objects(object_prefix)
    report.tos_listed_objects = len(inventory)
    matches: dict[str, set[str]] = defaultdict(set)
    targets: dict[str, tuple[int, str | None]] = {}
    seen_members: set[str] = set()

    with TemporaryDirectory(prefix="question-assets-") as staging_dir:
        staged_files: dict[str, str] = {}
        with tarfile.open(fileobj=archive, mode="r|gz") as bundle:
            for member in bundle:
                member_name = _normal_path(member.name)
                if not member.isfile() or not _is_image(member_name):
                    continue
                report.archive_image_members += 1
                if report.archive_image_members % 1_000 == 0:
                    print(
                        f"scanned {report.archive_image_members} archive images",
                        file=sys.stderr,
                        flush=True,
                    )
                asset_ids = _member_asset_ids(member_name, aliases)
                if not asset_ids:
                    continue
                report.archive_referenced_members += 1
                target_key = f"{object_prefix}{member_name}"
                duplicate_member = member_name in seen_members
                seen_members.add(member_name)
                for asset_id in asset_ids:
                    matches[asset_id].add(target_key)
                if duplicate_member:
                    continue
                targets[target_key] = (member.size, None)
                listed = inventory.get(target_key)
                listed_matches = _metadata_matches(
                    listed or ObjectMetadata(False),
                    size=member.size,
                    digest=None,
                )
                if mode != "apply" or listed_matches:
                    continue
                extracted = bundle.extractfile(member)
                if extracted is None:
                    continue
                staged_path = os.path.join(
                    staging_dir,
                    sha256(member_name.encode("utf-8")).hexdigest(),
                )
                digest = sha256()
                copied = 0
                with open(staged_path, "wb") as payload:
                    while chunk := extracted.read(1024 * 1024):
                        payload.write(chunk)
                        digest.update(chunk)
                        copied += len(chunk)
                if copied == member.size:
                    targets[target_key] = (copied, digest.hexdigest())
                    staged_files[target_key] = staged_path

        unique_targets = {
            next(iter(candidates))
            for asset in assets
            if asset.asset_id not in conflicting_assets
            and len(candidates := matches.get(asset.asset_id, set())) == 1
        }
        verified_keys: set[str] = set()
        for key in sorted(unique_targets):
            expected_size, digest = targets[key]
            listed = inventory.get(key)
            if _metadata_matches(
                listed or ObjectMetadata(False),
                size=expected_size,
                digest=None,
            ):
                verified_keys.add(key)
                report.objects_already_present += 1
                continue
            if listed is not None:
                refreshed = object_store.head(key)
                if _metadata_matches(refreshed, size=expected_size, digest=digest):
                    verified_keys.add(key)
                    report.objects_already_present += 1
                else:
                    report.objects_failed_verification += 1
                continue
            report.objects_missing += 1
            if mode != "apply":
                continue
            staged_path = staged_files.get(key)
            if staged_path is None or digest is None:
                report.objects_failed_verification += 1
                continue
            with open(staged_path, "rb") as payload:
                object_store.put(
                    key=key,
                    content=payload,
                    size=expected_size,
                    sha256_hex=digest,
                    content_type=mimetypes.guess_type(key)[0] or "application/octet-stream",
                )
            after = object_store.head(key)
            if _metadata_matches(
                after,
                size=expected_size,
                digest=digest,
                require_digest=True,
            ):
                verified_keys.add(key)
                report.objects_uploaded += 1
                if report.objects_uploaded % 1_000 == 0:
                    print(
                        f"uploaded {report.objects_uploaded} objects",
                        file=sys.stderr,
                        flush=True,
                    )
            else:
                report.objects_failed_verification += 1

        updates: dict[str, str] = {}
        for asset in assets:
            if asset.asset_id in conflicting_assets:
                report.ambiguous_assets += 1
                continue
            candidates = matches.get(asset.asset_id, set())
            if not candidates:
                report.unresolved_assets += 1
            elif len(candidates) != 1:
                report.ambiguous_assets += 1
            else:
                report.resolved_assets += 1
                target = next(iter(candidates))
                if target in verified_keys and asset.object_key != target:
                    updates[asset.asset_id] = target

        report.unresolved_asset_samples = tuple(
            asset.asset_id
            for asset in assets
            if asset.asset_id not in conflicting_assets and not matches.get(asset.asset_id)
        )[:20]
        report.ambiguous_asset_samples = tuple(
            asset.asset_id
            for asset in assets
            if asset.asset_id in conflicting_assets or len(matches.get(asset.asset_id, set())) > 1
        )[:20]
        if mode == "apply" and updates:
            publication = repository.apply_verified_updates(updates)
            report.asset_rows_updated = publication.updated_rows
            report.questions_republished = publication.published_questions
            report.questions_not_republished = publication.skipped_questions
            report.publication_failures = len(publication.failed_question_ids)
            report.publication_failure_samples = publication.failed_question_ids[:20]
    return report


def _asset_alias_index(
    assets: Iterable[AssetReference],
) -> tuple[dict[str, set[str]], set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    conflicting: set[str] = set()
    for asset in assets:
        canonical = {
            member
            for raw in (asset.image_src, asset.object_key)
            if (member := _canonical_archive_member(raw)) is not None
        }
        maximum_depth = max(
            (len(PurePosixPath(value).parts) for value in canonical),
            default=0,
        )
        candidates = {
            value for value in canonical if len(PurePosixPath(value).parts) == maximum_depth
        }
        if len(candidates) != 1:
            conflicting.add(asset.asset_id)
            continue
        result[next(iter(candidates))].add(asset.asset_id)
    return result, conflicting


def _canonical_archive_member(raw: str) -> str | None:
    value = unquote(urlsplit(raw).path if "://" in raw else raw)
    value = _normal_path(value)
    if not value:
        return None
    root_marker = f"{ARCHIVE_ROOT}/"
    root_at = value.find(root_marker)
    if root_at >= 0:
        member = value[root_at:]
        return member if _is_image(member) else None
    prefixes = (
        OBJECT_PREFIX,
        "data/question-bank-export/files/",
        "source-question-bank/",
        "files/",
    )
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if value.startswith(prefix):
                value = value[len(prefix) :]
                changed = True
                break
    if not value or not _is_image(value):
        return None
    return f"{ARCHIVE_ROOT}/{value}"


def _member_asset_ids(member_name: str, aliases: Mapping[str, set[str]]) -> set[str]:
    member_aliases = {member_name}
    parts = PurePosixPath(member_name).parts
    if len(parts) >= 3 and parts[0] == ARCHIVE_ROOT:
        member_aliases.add("/".join((ARCHIVE_ROOT, *parts[2:])))
    result: set[str] = set()
    for alias in member_aliases:
        result.update(aliases.get(alias, ()))
    return result


def _normal_path(value: str) -> str:
    normalized = str(PurePosixPath(value.strip().replace("\\", "/")))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/") if normalized != "." else ""


def _is_image(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in IMAGE_SUFFIXES


def _metadata_matches(
    metadata: ObjectMetadata,
    *,
    size: int,
    digest: str | None,
    require_digest: bool = False,
) -> bool:
    if not metadata.exists or metadata.size != size:
        return False
    if digest is None:
        return True
    if metadata.sha256_hex is None:
        return not require_digest
    return metadata.sha256_hex == digest


def _tos_status(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _open_archive(path: str) -> tuple[BinaryIO, bool]:
    if path == "-":
        return sys.stdin.buffer, False
    return open(path, "rb"), True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "apply"))
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--archive", required=True, help="tar.gz path, or - for stdin")
    parser.add_argument("--object-prefix", default=OBJECT_PREFIX)
    parser.add_argument("--tos-access-key", default=os.getenv("TOS_ACCESS_KEY", ""))
    parser.add_argument("--tos-secret-key", default=os.getenv("TOS_SECRET_KEY", ""))
    parser.add_argument("--tos-endpoint", default=os.getenv("TOS_ENDPOINT", ""))
    parser.add_argument("--tos-region", default=os.getenv("TOS_REGION", ""))
    parser.add_argument("--tos-bucket", default=os.getenv("TOS_BUCKET", ""))
    return parser


def main() -> None:
    args = _parser().parse_args()
    engine = create_engine(args.database_url)
    store = TosObjectStore(
        access_key=args.tos_access_key,
        secret_key=args.tos_secret_key,
        endpoint=args.tos_endpoint,
        region=args.tos_region,
        bucket=args.tos_bucket,
    )
    archive, should_close = _open_archive(args.archive)
    try:
        with Session(engine) as session:
            report = repair_question_assets(
                mode=args.mode,
                repository=SQLAlchemyQuestionAssetRepository(session),
                object_store=store,
                archive=archive,
                object_prefix=args.object_prefix.rstrip("/") + "/",
            )
    finally:
        if should_close:
            archive.close()
        engine.dispose()
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
