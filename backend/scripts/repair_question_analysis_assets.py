#!/usr/bin/env python3
"""Recover original explanation images omitted from the question-bank import.

The source JSONL keeps the authoritative ``original_analysis_html`` while the
product database keeps only its text.  This repair reads the HTML image refs,
uploads the referenced source files to the existing public question-asset
prefix, and records immutable object metadata inside
``product.question_explanations.final_analysis_asset_refs``.

The command is targeted by default.  A full-bank run requires explicit
``--all`` so an operator cannot accidentally enqueue tens of thousands of
objects while repairing one question.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import quote
from urllib.parse import unquote, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

try:
    from scripts.repair_question_asset_storage import ObjectMetadata, TosObjectStore
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from repair_question_asset_storage import ObjectMetadata, TosObjectStore


OBJECT_PREFIX = "data/question-bank-export/files/source-question-bank/"
IMAGE_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})
MIN_STANDALONE_IMAGE_SPAN_PIXELS = 64


@dataclass(frozen=True, slots=True)
class SourceBinding:
    question_id: str
    source_question_id: str
    source_record_id: str


@dataclass(frozen=True, slots=True)
class AnalysisAsset:
    image_ordinal: int
    image_src: str
    source_path: Path
    object_key: str
    content_sha256: str
    mime_type: str
    byte_size: int
    pixel_width: int
    pixel_height: int

    def reference(self) -> dict[str, object]:
        return {
            "imageOrdinal": self.image_ordinal,
            "imageSrc": self.image_src,
            "objectKey": self.object_key,
            "contentSha256": self.content_sha256,
            "mimeType": self.mime_type,
            "byteSize": self.byte_size,
            "pixelWidth": self.pixel_width,
            "pixelHeight": self.pixel_height,
        }


@dataclass(frozen=True, slots=True)
class RepairReport:
    mode: str
    selected_questions: int
    matched_source_records: int
    resolved_assets: int
    missing_assets: int
    objects_already_present: int
    objects_uploaded: int
    objects_unverified: int
    explanations_updated: int
    explanations_skipped: int


class _ImageSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.casefold() != "img":
            return
        normalized = {
            name.casefold(): value.strip()
            for name, value in attrs
            if value and value.strip()
        }
        # Fenbi stores degree signs and formula glyphs as tiny inline images.
        # They belong in rich-text/formula rendering, not as standalone Bot images.
        if normalized.get("flag", "").casefold() == "tex":
            return
        source = normalized.get("src")
        if source and source not in self.sources:
            self.sources.append(source)


def extract_analysis_assets(
    record: dict[str, object],
    *,
    asset_root: Path,
) -> tuple[AnalysisAsset, ...]:
    html = str(
        record.get("original_analysis_html")
        or record.get("original_solution_html")
        or ""
    ).strip()
    if not html:
        return ()
    parser = _ImageSourceParser()
    parser.feed(html)
    province = str(record.get("province") or "").strip()
    assets: list[AnalysisAsset] = []
    for ordinal, raw_source in enumerate(parser.sources, start=1):
        resolved = _resolve_source_asset(
            raw_source,
            province=province,
            asset_root=asset_root,
        )
        if resolved is None:
            continue
        relative_path, source_path = resolved
        try:
            with Image.open(source_path) as image:
                pixel_width, pixel_height = image.size
        except (OSError, UnidentifiedImageError):
            continue
        # Tiny inline sprites only make sense inside the original HTML sentence.
        # Sending them as separate WeChat images would turn one explanation into
        # dozens of meaningless glyph messages.
        if max(pixel_width, pixel_height) < MIN_STANDALONE_IMAGE_SPAN_PIXELS:
            continue
        payload = source_path.read_bytes()
        mime_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        assets.append(
            AnalysisAsset(
                image_ordinal=ordinal,
                image_src=raw_source,
                source_path=source_path,
                object_key=f"{OBJECT_PREFIX}{asset_root.name}/{relative_path.as_posix()}",
                content_sha256=sha256(payload).hexdigest(),
                mime_type=mime_type,
                byte_size=len(payload),
                pixel_width=pixel_width,
                pixel_height=pixel_height,
            )
        )
    return tuple(assets)


def repair_analysis_assets(
    session: Session,
    *,
    mode: str,
    source_jsonl: Path,
    asset_root: Path,
    object_store: TosObjectStore,
    question_ids: tuple[str, ...],
    all_questions: bool,
    public_verifier: Callable[[AnalysisAsset], bool] | None = None,
) -> RepairReport:
    if mode not in {"audit", "apply"}:
        raise ValueError("mode must be audit or apply")
    if not all_questions and not question_ids:
        raise ValueError("select at least one --question-id or pass --all")
    bindings = _source_bindings(
        session,
        question_ids=question_ids,
        all_questions=all_questions,
    )
    by_record: dict[str, list[SourceBinding]] = {}
    for binding in bindings:
        by_record.setdefault(binding.source_record_id, []).append(binding)

    candidates: dict[str, tuple[AnalysisAsset, ...]] = {}
    matched_records = 0
    unresolved = 0
    with source_jsonl.open("r", encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            record_id = str(record.get("id") or "")
            selected = by_record.get(record_id)
            if not selected:
                continue
            matched_records += 1
            assets = extract_analysis_assets(record, asset_root=asset_root)
            expected_sources = _image_sources(record)
            province = str(record.get("province") or "").strip()
            unresolved += sum(
                _resolve_source_asset(
                    source,
                    province=province,
                    asset_root=asset_root,
                )
                is None
                for source in expected_sources
            )
            for binding in selected:
                candidates[binding.question_id] = assets

    already_present = 0
    uploaded = 0
    unverified = 0
    updated = 0
    skipped = 0
    verifier = public_verifier or public_object_matches
    for question_id, assets in sorted(candidates.items()):
        verified: list[AnalysisAsset] = []
        for asset in assets:
            metadata = object_store.head(asset.object_key)
            if _metadata_matches(metadata, asset) or (
                metadata.exists
                and metadata.size == asset.byte_size
                and metadata.sha256_hex is None
                and verifier(asset)
            ):
                already_present += 1
                verified.append(asset)
                continue
            if metadata.exists:
                unverified += 1
                continue
            if mode != "apply":
                continue
            with asset.source_path.open("rb") as payload:
                object_store.put(
                    key=asset.object_key,
                    content=payload,
                    size=asset.byte_size,
                    sha256_hex=asset.content_sha256,
                    content_type=asset.mime_type,
                )
            if not _metadata_matches(object_store.head(asset.object_key), asset):
                raise RuntimeError(f"uploaded explanation asset failed verification: {asset.object_key}")
            uploaded += 1
            verified.append(asset)
        if mode == "apply" and len(verified) == len(assets):
            changed = session.execute(
                text(
                    """
                    update product.question_explanations
                    set final_analysis_asset_refs = jsonb_set(
                      coalesce(final_analysis_asset_refs, '{}'::jsonb),
                      '{assets}', cast(:assets as jsonb), true
                    )
                    where question_id = :question_id
                      and analysis_source = 'original_analysis'
                    """
                ),
                {
                    "question_id": question_id,
                    "assets": json.dumps(
                        [asset.reference() for asset in verified],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ).rowcount
            updated += int(changed or 0)
        elif mode == "apply":
            skipped += 1
    if mode == "apply":
        session.commit()
    else:
        session.rollback()
    return RepairReport(
        mode=mode,
        selected_questions=len({binding.question_id for binding in bindings}),
        matched_source_records=matched_records,
        resolved_assets=sum(len(value) for value in candidates.values()),
        missing_assets=unresolved,
        objects_already_present=already_present,
        objects_uploaded=uploaded,
        objects_unverified=unverified,
        explanations_updated=updated,
        explanations_skipped=skipped,
    )


def _source_bindings(
    session: Session,
    *,
    question_ids: tuple[str, ...],
    all_questions: bool,
) -> tuple[SourceBinding, ...]:
    filters = [
        "explanation.analysis_source = 'original_analysis'",
        "explanation.analysis_status = 'preserve_original_analysis_image'",
        "occurrence.source_occurrence_question_key is not null",
    ]
    params: dict[str, object] = {}
    if not all_questions:
        filters.append("question.question_id = any(cast(:question_ids as text[]))")
        params["question_ids"] = list(question_ids)
    rows = session.execute(
        text(
            f"""
            select distinct on (question.question_id)
                   question.question_id,
                   occurrence.source_question_id,
                   occurrence.source_occurrence_question_key
            from product.questions question
            join product.question_explanations explanation
              on explanation.question_id = question.question_id
            join product.question_occurrences occurrence
              on occurrence.question_id = question.question_id
            where {' and '.join(filters)}
            order by question.question_id,
                     occurrence.canonical_occurrence_rank nulls last,
                     occurrence.occurrence_id
            """
        ),
        params,
    ).mappings()
    return tuple(
        SourceBinding(
            question_id=str(row["question_id"]),
            source_question_id=str(row["source_question_id"]),
            source_record_id=str(row["source_occurrence_question_key"]),
        )
        for row in rows
    )


def _image_sources(record: dict[str, object]) -> tuple[str, ...]:
    html = str(
        record.get("original_analysis_html")
        or record.get("original_solution_html")
        or ""
    ).strip()
    parser = _ImageSourceParser()
    parser.feed(html)
    return tuple(parser.sources)


def _resolve_source_asset(
    raw_source: str,
    *,
    province: str,
    asset_root: Path,
) -> tuple[PurePosixPath, Path] | None:
    parsed = urlsplit(raw_source)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    normalized = PurePosixPath(unquote(parsed.path).replace("\\", "/"))
    if (
        normalized.is_absolute()
        or normalized.suffix.casefold() not in IMAGE_SUFFIXES
        or any(part in {"", ".", ".."} for part in normalized.parts)
    ):
        return None
    relatives = (
        (PurePosixPath(province) / normalized) if province else normalized,
        normalized,
    )
    root = asset_root.resolve()
    for relative in relatives:
        candidate = (root / Path(*relative.parts)).resolve()
        if root not in candidate.parents or not candidate.is_file():
            continue
        return relative, candidate
    return None


def _metadata_matches(metadata: ObjectMetadata, asset: AnalysisAsset) -> bool:
    return (
        metadata.exists
        and metadata.size == asset.byte_size
        and metadata.sha256_hex == asset.content_sha256
    )


def public_object_matches(asset: AnalysisAsset) -> bool:
    encoded_key = quote(asset.object_key, safe="/")
    url = f"https://img.xingguoxian.cn/{encoded_key}"
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as response:
            response.raise_for_status()
            digest = sha256()
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > asset.byte_size:
                    return False
                digest.update(chunk)
    except httpx.HTTPError:
        return False
    return size == asset.byte_size and digest.hexdigest() == asset.content_sha256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("audit", "apply"), default="audit")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--source-jsonl", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--question-id", action="append", default=[])
    parser.add_argument("--all", action="store_true", dest="all_questions")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    settings = {
        "access_key": os.getenv("TOS_ACCESS_KEY", ""),
        "secret_key": os.getenv("TOS_SECRET_KEY", ""),
        "endpoint": os.getenv("TOS_ENDPOINT", ""),
        "region": os.getenv("TOS_REGION", ""),
        "bucket": os.getenv("TOS_BUCKET", "gongkao-dazi-prod"),
    }
    engine = create_engine(args.database_url, pool_pre_ping=True, future=True)
    try:
        with Session(engine) as session:
            report = repair_analysis_assets(
                session,
                mode=args.mode,
                source_jsonl=args.source_jsonl,
                asset_root=args.asset_root,
                object_store=TosObjectStore(**settings),
                question_ids=tuple(args.question_id),
                all_questions=bool(args.all_questions),
            )
    finally:
        engine.dispose()
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
